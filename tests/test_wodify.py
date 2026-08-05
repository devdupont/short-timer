"""Wodify intake, exercised against HTML fixtures.

Neither route has been run against a live gym (see wodify.py), so these pin the
behaviour we *can* pin offline: envelope handling, the failure modes that must
degrade to an empty feed rather than an exception, and the cache key — which is
the one thing here that would be a security bug if it were wrong.
"""

from datetime import date

import httpx
import pytest
import respx

from short_timer.gym_cache import gym_fingerprint
from short_timer.html_text import extract_text
from short_timer.models import GymProvider
from short_timer.wodify import (
    fetch_member_wod,
    fetch_owner_wod,
    fetch_recent_member_wods,
)

PROGRAM_API = "https://api.wodify.com/v1/workouts/formattedworkout"
WHITEBOARD = "https://app.wodify.com/Performance/PublicWhiteboard.aspx"

WHITEBOARD_HTML = """
<html>
  <head><style>.x { color: red }</style></head>
  <body>
    <nav>Home | Schedule | Login</nav>
    <div class="whiteboard">
      <h2>CrossFit</h2>
      <p>For Time:</p>
      <p>21-15-9</p>
      <p>Thrusters 95/65 lb</p>
      <p>Pull-ups</p>
    </div>
    <footer>&copy; Mousetrap Fitness</footer>
    <script>analytics()</script>
  </body>
</html>
"""

FORMATTED_WOD_HTML = (
    "<div><strong>Metcon</strong><p>AMRAP 12:</p>"
    "<p>10 Burpees</p><p>15 Kettlebell Swings 53 lb</p></div>"
)

DAY = date(2026, 7, 20)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(follow_redirects=True) as ac:
        yield ac


# --- HTML extraction ---------------------------------------------------------


def test_extract_text_drops_chrome_and_keeps_workout() -> None:
    text = extract_text(WHITEBOARD_HTML)
    assert "Thrusters 95/65 lb" in text
    assert "21-15-9" in text
    # Nav, footer, script and style are all chrome.
    assert "Login" not in text
    assert "analytics()" not in text
    assert "color: red" not in text
    assert "Mousetrap" not in text


def test_extract_text_collapses_blank_lines() -> None:
    assert "\n\n" not in extract_text("<p>a</p><br><br><p>b</p>")


# --- Member route (public whiteboard) ---------------------------------------


@respx.mock
async def test_member_fetch_returns_workout(client: httpx.AsyncClient) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=WHITEBOARD_HTML))
    wod = await fetch_member_wod(client, DAY, whiteboard_key="wb-key")
    assert wod is not None
    assert wod.date == DAY
    assert "Thrusters 95/65 lb" in wod.text
    # The link must not carry the credential — it's cached and sent to clients.
    assert "wb-key" not in wod.url
    assert "WhiteboardKey" not in wod.url


@respx.mock
async def test_member_fetch_sends_expected_params(client: httpx.AsyncClient) -> None:
    route = respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=WHITEBOARD_HTML))
    await fetch_member_wod(
        client, DAY, whiteboard_key="wb-key", location="Main", program="CrossFit"
    )
    request = route.calls.last.request
    assert request.url.params["WhiteboardKey"] == "wb-key"
    # Wodify wants US-style dates here, not ISO.
    assert request.url.params["Date"] == "07/20/2026"
    assert request.url.params["LocationName"] == "Main"
    assert request.url.params["ProgramName"] == "CrossFit"


@respx.mock
async def test_member_unpublished_day_is_skipped(client: httpx.AsyncClient) -> None:
    """An empty board is a 200, and must read as "nothing to show"."""
    empty = "<html><body><nav>Home</nav><div class='whiteboard'></div></body></html>"
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=empty))
    assert await fetch_member_wod(client, DAY, whiteboard_key="wb-key") is None


@respx.mock
async def test_member_http_error_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(500))
    assert await fetch_member_wod(client, DAY, whiteboard_key="wb-key") is None


@respx.mock
async def test_member_network_failure_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get(WHITEBOARD).mock(side_effect=httpx.ConnectError("boom"))
    assert await fetch_member_wod(client, DAY, whiteboard_key="wb-key") is None


@respx.mock
async def test_recent_member_wods_skips_bad_days() -> None:
    """One failing day must not empty the whole feed."""
    responses = [
        httpx.Response(200, html=WHITEBOARD_HTML),
        httpx.Response(500),
        httpx.Response(200, html=WHITEBOARD_HTML),
    ]
    respx.get(WHITEBOARD).mock(side_effect=responses)
    wods = await fetch_recent_member_wods(3, whiteboard_key="wb-key", today=DAY)
    assert len(wods) == 2


# --- Owner route (Program API) ----------------------------------------------


@respx.mock
async def test_owner_fetch_reads_formatted_wod(client: httpx.AsyncClient) -> None:
    respx.get(PROGRAM_API).mock(
        return_value=httpx.Response(200, json={"APIWod": {"FormattedWOD": FORMATTED_WOD_HTML}})
    )
    wod = await fetch_owner_wod(client, DAY, api_key="k", location="Main", program="CrossFit")
    assert wod is not None
    assert "15 Kettlebell Swings 53 lb" in wod.text
    assert "<p>" not in wod.text


@respx.mock
async def test_owner_fetch_sends_api_key_header(client: httpx.AsyncClient) -> None:
    route = respx.get(PROGRAM_API).mock(
        return_value=httpx.Response(200, json={"APIWod": {"FormattedWOD": FORMATTED_WOD_HTML}})
    )
    await fetch_owner_wod(client, DAY, api_key="secret-key", location="Main", program="CF")
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "secret-key"
    assert request.url.params["date"] == "2026-07-20"
    assert request.url.params["location"] == "Main"


@respx.mock
async def test_owner_fetch_tolerates_envelope_variations(client: httpx.AsyncClient) -> None:
    """Field casing is the least-verified part of this route."""
    for payload in (
        {"APIWod": {"FormattedWOD": FORMATTED_WOD_HTML}},
        {"apiWod": {"formatted_wod": FORMATTED_WOD_HTML}},
        {"FormattedWod": FORMATTED_WOD_HTML},
        [{"FormattedWOD": FORMATTED_WOD_HTML}],
    ):
        respx.get(PROGRAM_API).mock(return_value=httpx.Response(200, json=payload))
        wod = await fetch_owner_wod(client, DAY, api_key="k", location="L", program="P")
        assert wod is not None, payload
        assert "10 Burpees" in wod.text


@respx.mock
async def test_owner_rejected_key_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get(PROGRAM_API).mock(return_value=httpx.Response(401))
    assert await fetch_owner_wod(client, DAY, api_key="bad", location="L", program="P") is None


@respx.mock
async def test_owner_non_json_response_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get(PROGRAM_API).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    assert await fetch_owner_wod(client, DAY, api_key="k", location="L", program="P") is None


@respx.mock
async def test_owner_empty_workout_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get(PROGRAM_API).mock(return_value=httpx.Response(200, json={"APIWod": {}}))
    assert await fetch_owner_wod(client, DAY, api_key="k", location="L", program="P") is None


# --- Cache key ---------------------------------------------------------------
# The one place a mistake would be a security bug rather than a broken feed.


def test_different_gyms_never_share_a_cache_key() -> None:
    assert gym_fingerprint("gym-a-key", GymProvider.WODIFY_MEMBER) != gym_fingerprint(
        "gym-b-key", GymProvider.WODIFY_MEMBER
    )


def test_same_gym_shares_a_cache_key() -> None:
    """Two members of one gym should hit the same entries."""
    assert gym_fingerprint("shared-key", GymProvider.WODIFY_MEMBER) == gym_fingerprint(
        "shared-key", GymProvider.WODIFY_MEMBER
    )


def test_routes_do_not_collide() -> None:
    """The two routes format the same workout differently; don't conflate them."""
    assert gym_fingerprint("same-credential", GymProvider.WODIFY_MEMBER) != gym_fingerprint(
        "same-credential", GymProvider.WODIFY_OWNER
    )


def test_fingerprint_does_not_leak_the_credential() -> None:
    secret = "super-secret-whiteboard-key"
    assert secret not in gym_fingerprint(secret, GymProvider.WODIFY_MEMBER)
