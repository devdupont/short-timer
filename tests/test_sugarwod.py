"""The SugarWOD intake, and the provider registry it plugs into."""

from datetime import date

import httpx
import pytest
import respx

from shortimer.model.gym import GymConnection, GymProvider
from shortimer.service import sugarwod
from shortimer.service.gym_providers import PROVIDERS, all_info, spec_for
from shortimer.service.sugarwod import _windows, fetch_recent_owner_wods

API = "https://api.sugarwod.com/v2/workouts"

FRAN = "21-15-9 reps for time of Thrusters 95/65 lb and Pull-ups"


def _payload(*workouts: dict[str, object]) -> dict[str, object]:
    """A JSON:API collection document, the shape the v2 API answers with."""
    return {
        "data": [
            {"type": "workouts", "id": f"w{index}", "attributes": attributes}
            for index, attributes in enumerate(workouts)
        ]
    }


# --- Date-range windowing ----------------------------------------------------


def test_a_short_range_is_one_window() -> None:
    spans = _windows(date(2026, 8, 1), date(2026, 8, 5))
    assert spans == [(date(2026, 8, 1), date(2026, 8, 5))]


def test_a_long_range_is_split_to_the_api_limit() -> None:
    """The API rejects anything wider than a week, so a fortnight is two calls."""
    spans = _windows(date(2026, 7, 23), date(2026, 8, 5))
    assert spans == [
        (date(2026, 7, 23), date(2026, 7, 29)),
        (date(2026, 7, 30), date(2026, 8, 5)),
    ]
    assert all((end - start).days < sugarwod.MAX_RANGE_DAYS for start, end in spans)


def test_windows_cover_the_range_without_gaps_or_overlap() -> None:
    spans = _windows(date(2026, 1, 1), date(2026, 2, 15))
    days = [d for start, end in spans for d in _each_day(start, end)]
    assert len(days) == len(set(days)) == 46


def _each_day(start: date, end: date) -> list[date]:
    from datetime import timedelta

    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# --- Reading a response ------------------------------------------------------


@respx.mock
async def test_a_workout_becomes_a_gym_wod() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=_payload({"title": "Fran", "description": FRAN, "date_int": 20260804}),
        )
    )
    wods = await fetch_recent_owner_wods(3, api_key="key", today=date(2026, 8, 4))

    assert len(wods) == 1
    assert wods[0].title == "Fran"
    assert "Thrusters" in wods[0].text
    assert wods[0].date == date(2026, 8, 4)
    assert wods[0].provider is GymProvider.SUGARWOD_OWNER


@respx.mock
async def test_the_api_key_travels_in_a_header_not_the_query_string() -> None:
    """A key in a URL ends up in access logs; this one reads a gym's programming."""
    route = respx.get(API).mock(return_value=httpx.Response(200, json=_payload()))
    await fetch_recent_owner_wods(2, api_key="secret-key", today=date(2026, 8, 4))

    request = route.calls[0].request
    assert request.headers["Authorization"] == "secret-key"
    assert "secret-key" not in str(request.url)


@respx.mock
async def test_a_track_filter_is_passed_through_when_set() -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json=_payload()))
    await fetch_recent_owner_wods(2, api_key="k", track_id="wod", today=date(2026, 8, 4))
    assert route.calls[0].request.url.params["track_id"] == "wod"


@respx.mock
async def test_no_track_means_every_track() -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json=_payload()))
    await fetch_recent_owner_wods(2, api_key="k", today=date(2026, 8, 4))
    assert "track_id" not in route.calls[0].request.url.params


@pytest.mark.parametrize(
    "attributes",
    [
        {"description": FRAN, "date_int": 20260804},
        {"description": FRAN, "date": "2026-08-04"},
        {"description": FRAN, "scheduledDate": "2026-08-04T06:00:00Z"},
    ],
)
@respx.mock
async def test_the_date_is_read_however_it_is_spelled(attributes: dict[str, object]) -> None:
    """Field names come from documentation, not a live response — tolerate variants."""
    respx.get(API).mock(return_value=httpx.Response(200, json=_payload(attributes)))
    wods = await fetch_recent_owner_wods(3, api_key="k", today=date(2026, 8, 4))
    assert wods[0].date == date(2026, 8, 4)


@respx.mock
async def test_an_unreadable_date_falls_back_to_the_day_we_asked_for() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(200, json=_payload({"description": FRAN, "date": "not-a-date"}))
    )
    wods = await fetch_recent_owner_wods(1, api_key="k", today=date(2026, 8, 4))
    assert wods[0].date == date(2026, 8, 4)


@respx.mock
async def test_html_in_a_description_is_reduced_to_text() -> None:
    """Coaches paste rich text, so a description may arrive as an HTML fragment."""
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=_payload({"description": f"<div><p>{FRAN}</p><script>x()</script></div>"}),
        )
    )
    wods = await fetch_recent_owner_wods(1, api_key="k", today=date(2026, 8, 4))
    assert "<p>" not in wods[0].text
    assert "x()" not in wods[0].text
    assert "Thrusters" in wods[0].text


@respx.mock
async def test_a_workout_in_two_tracks_appears_once() -> None:
    """Windows are fetched concurrently; the same day must not double up."""
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                {"title": "Fran", "description": FRAN, "date_int": 20260804},
                {"title": "Fran (RX)", "description": FRAN, "date_int": 20260804},
            ),
        )
    )
    wods = await fetch_recent_owner_wods(3, api_key="k", today=date(2026, 8, 4))
    assert len(wods) == 1


@respx.mock
async def test_results_come_back_newest_first() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                {"description": FRAN, "date_int": 20260802},
                {"description": FRAN, "date_int": 20260804},
                {"description": FRAN, "date_int": 20260803},
            ),
        )
    )
    wods = await fetch_recent_owner_wods(5, api_key="k", today=date(2026, 8, 4))
    assert [wod.date.day for wod in wods] == [4, 3, 2]


# --- Failure modes -----------------------------------------------------------


@pytest.mark.parametrize("status_code", [400, 401, 403, 429, 500])
@respx.mock
async def test_an_upstream_failure_yields_no_workouts_rather_than_raising(
    status_code: int,
) -> None:
    """One bad platform response shouldn't empty — or explode — the feed."""
    respx.get(API).mock(return_value=httpx.Response(status_code, json={}))
    assert await fetch_recent_owner_wods(3, api_key="k", today=date(2026, 8, 4)) == []


@respx.mock
async def test_a_network_error_yields_no_workouts() -> None:
    respx.get(API).mock(side_effect=httpx.ConnectError("down"))
    assert await fetch_recent_owner_wods(3, api_key="k", today=date(2026, 8, 4)) == []


@respx.mock
async def test_a_non_json_body_yields_no_workouts() -> None:
    respx.get(API).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    assert await fetch_recent_owner_wods(3, api_key="k", today=date(2026, 8, 4)) == []


@respx.mock
async def test_a_workout_with_no_description_is_skipped() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(200, json=_payload({"title": "Rest", "date_int": 20260804}))
    )
    assert await fetch_recent_owner_wods(1, api_key="k", today=date(2026, 8, 4)) == []


@respx.mock
async def test_a_stub_too_short_to_be_a_workout_is_skipped() -> None:
    """A login redirect or error page reduces to almost nothing once stripped."""
    respx.get(API).mock(return_value=httpx.Response(200, json=_payload({"description": "Sign in"})))
    assert await fetch_recent_owner_wods(1, api_key="k", today=date(2026, 8, 4)) == []


@respx.mock
async def test_one_failed_window_does_not_lose_the_other() -> None:
    """A fortnight is two calls; the half that worked should still arrive."""
    responses = [
        httpx.Response(500, json={}),
        httpx.Response(200, json=_payload({"description": FRAN, "date_int": 20260804})),
    ]
    respx.get(API).mock(side_effect=responses)
    wods = await fetch_recent_owner_wods(14, api_key="k", today=date(2026, 8, 4))
    assert len(wods) == 1


# --- The registry ------------------------------------------------------------


def test_every_provider_is_registered() -> None:
    """A member of the enum with no spec would 500 the moment someone saved it."""
    assert set(PROVIDERS) == set(GymProvider)


def test_every_provider_is_offered_in_settings() -> None:
    assert {info.provider for info in all_info()} == set(GymProvider)


def test_a_connection_needs_its_required_fields_to_be_usable() -> None:
    """Wodify's owner route filters on exact names, so both are mandatory."""
    spec = spec_for(GymProvider.WODIFY_OWNER)
    from shortimer.cache.crypto import SecretBox

    bare = GymConnection(
        provider=GymProvider.WODIFY_OWNER,
        credential=SecretBox(ciphertext="x"),
        enabled=True,
    )
    assert spec.is_usable(bare) is False
    assert spec.is_usable(bare.model_copy(update={"location": "Main"})) is False
    complete = bare.model_copy(update={"location": "Main", "program": "CrossFit"})
    assert spec.is_usable(complete) is True


def test_a_provider_with_no_required_fields_needs_only_a_credential() -> None:
    from shortimer.cache.crypto import SecretBox

    connection = GymConnection(
        provider=GymProvider.SUGARWOD_OWNER,
        credential=SecretBox(ciphertext="x"),
        enabled=True,
    )
    assert spec_for(GymProvider.SUGARWOD_OWNER).is_usable(connection) is True


def test_a_disabled_connection_is_never_usable() -> None:
    from shortimer.cache.crypto import SecretBox

    connection = GymConnection(
        provider=GymProvider.SUGARWOD_OWNER,
        credential=SecretBox(ciphertext="x"),
        enabled=False,
    )
    assert spec_for(GymProvider.SUGARWOD_OWNER).is_usable(connection) is False


def test_sugarwod_does_not_advertise_a_location_field() -> None:
    """It scopes by track; offering a location box would just confuse people."""
    info = spec_for(GymProvider.SUGARWOD_OWNER).info
    assert info.location is None
    assert info.program is not None and info.program.label == "Track"


async def test_a_rejected_key_is_logged_even_though_it_arrives_as_a_400(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verified against the live API: a bad key is 400, not 401.

    Warning only on 401/403 would make the most likely misconfiguration —
    someone pasting the wrong key — completely silent, which is precisely the
    case `GET /api/gym/health` exists to make diagnosable.
    """
    with respx.mock:
        respx.get(API).mock(
            return_value=httpx.Response(
                400, json={"errors": {"message": "Invalid API Key.", "code": "Key: undefined"}}
            )
        )
        with caplog.at_level("WARNING"):
            assert await fetch_recent_owner_wods(3, api_key="wrong", today=date(2026, 8, 4)) == []

    assert "Invalid API Key." in caplog.text
    # The credential itself must never reach the log.
    assert "wrong" not in caplog.text.replace("Invalid API Key.", "")


async def test_a_missing_key_is_distinguishable_from_a_wrong_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SugarWOD says which it is, and that difference is the whole diagnostic."""
    with respx.mock:
        respx.get(API).mock(
            return_value=httpx.Response(
                400, json={"errors": {"message": "No API Key found in request.", "code": 999999}}
            )
        )
        with caplog.at_level("WARNING"):
            await fetch_recent_owner_wods(3, api_key="", today=date(2026, 8, 4))

    assert "No API Key found in request." in caplog.text
