from datetime import date

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from short_timer import concept2
from short_timer.app import app
from short_timer.concept2 import fetch_wod, parse_wod_page
from short_timer.concept2_cache import (
    CACHE_DAYS,
    ensure_wods_parsed,
    get_wods,
    read_cached_wods,
    refresh_concept2_cache,
)
from short_timer.db import get_concept2_cache_collection
from short_timer.models import Workout, WorkoutMode
from short_timer.parse_cache import find_parse


def _page(title: str, description: str | None = "Seven alternating intervals.") -> str:
    """Trimmed from a real Honorboard page.

    The workout heading and description sit in `section.content`, ahead of a
    few hundred leaderboard rows — and there's a decoy `h3` in the nav.
    """
    body = f"<h3>{title}</h3>"
    if description is not None:
        body += f"<p><strong>{description}</strong></p>"
    return f"""
    <html><body>
      <nav><h3>Not the workout</h3></nav>
      <section class="content">
        <h1>Workout of the Day</h1>
        <h2><a href="/wod/2026-07-21/rowerg">&laquo;</a> July 22, 2026</h2>
        {body}
        <table class="table"><tbody><tr><td>1</td><td>Someone</td></tr></tbody></table>
      </section>
    </body></html>
    """


_TITLE = "2/3/2/3/2/3/2 minutes with 1 minute rest"
_URL_PATTERN = r"https://log\.concept2\.com/wod/.*"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def authed_client(client: AsyncClient) -> AsyncClient:
    response = await client.post("/api/auth/login", json={"passcode": "test-passcode"})
    assert response.status_code == 204
    return client


def test_parse_keeps_heading_and_description() -> None:
    """The description is what disambiguates the heading, so both are kept."""
    wod = parse_wod_page(_page(_TITLE), date(2026, 7, 22))
    assert wod is not None
    assert wod.title == _TITLE
    assert wod.text == f"{_TITLE}\n\nSeven alternating intervals."
    assert wod.url == "https://log.concept2.com/wod/2026-07-22/rowerg"


def test_parse_tolerates_a_day_with_no_description() -> None:
    wod = parse_wod_page(_page(_TITLE, description=None), date(2026, 7, 22))
    assert wod is not None
    assert wod.text == _TITLE


def test_parse_ignores_headings_outside_the_content_section() -> None:
    """A miss must read as "no workout", not as some stray heading."""
    html = "<html><body><nav><h3>Not the workout</h3></nav></body></html>"
    assert parse_wod_page(html, date(2026, 7, 22)) is None


@respx.mock
async def test_fetch_treats_a_500_as_a_missing_day() -> None:
    """Dates outside the published range answer 500 rather than 404."""
    respx.get("https://log.concept2.com/wod/2027-01-01/rowerg").mock(return_value=Response(500))
    async with httpx.AsyncClient() as http:
        assert await fetch_wod(http, date(2027, 1, 1)) is None


@respx.mock
async def test_fetch_recent_skips_failures() -> None:
    respx.get("https://log.concept2.com/wod/2026-07-22/rowerg").mock(
        return_value=Response(200, text=_page(_TITLE))
    )
    respx.get("https://log.concept2.com/wod/2026-07-21/rowerg").mock(return_value=Response(500))
    wods = await concept2.fetch_recent_wods(2, today=date(2026, 7, 22))
    assert [w.date.isoformat() for w in wods] == ["2026-07-22"]


@respx.mock
async def test_refresh_only_fetches_days_it_does_not_have() -> None:
    """The steady-state cost of this feed is one request a day."""
    route = respx.route(url__regex=_URL_PATTERN).mock(
        return_value=Response(200, text=_page(_TITLE))
    )
    assert await refresh_concept2_cache(force=True, today=date(2026, 7, 21)) == CACHE_DAYS
    after_cold_start = route.call_count

    # A day later, only the new day is missing.
    assert await refresh_concept2_cache(force=True, today=date(2026, 7, 22)) == 1
    assert route.call_count == after_cold_start + 1

    # Same day again: nothing missing, so nothing fetched.
    assert await refresh_concept2_cache(force=True, today=date(2026, 7, 22)) == 0
    assert route.call_count == after_cold_start + 1


@respx.mock
async def test_refresh_fills_a_gap_left_by_downtime() -> None:
    respx.route(url__regex=_URL_PATTERN).mock(return_value=Response(200, text=_page(_TITLE)))
    await refresh_concept2_cache(force=True, today=date(2026, 7, 18))

    # Three days down, then back up: the missed days are still fetchable.
    assert await refresh_concept2_cache(force=True, today=date(2026, 7, 21)) == 3
    cached = {wod.date.isoformat() for wod in await read_cached_wods(CACHE_DAYS)}
    assert {"2026-07-19", "2026-07-20", "2026-07-21"} <= cached


@respx.mock
async def test_refresh_keeps_stale_cache_when_upstream_fails() -> None:
    respx.route(url__regex=_URL_PATTERN).mock(return_value=Response(200, text=_page(_TITLE)))
    await refresh_concept2_cache(force=True, today=date(2026, 7, 21))
    cached = await read_cached_wods(CACHE_DAYS)

    respx.route(url__regex=_URL_PATTERN).mock(return_value=Response(500))
    assert await refresh_concept2_cache(force=True, today=date(2026, 7, 22)) == 0
    assert len(await read_cached_wods(CACHE_DAYS)) == len(cached)


@respx.mock
async def test_wods_are_served_from_cache_without_refetching() -> None:
    route = respx.route(url__regex=_URL_PATTERN).mock(
        return_value=Response(200, text=_page(_TITLE))
    )
    assert await refresh_concept2_cache(force=True) > 0
    calls_after_refresh = route.call_count

    assert len(await get_wods(3)) > 0
    assert len(await get_wods(3)) > 0
    assert route.call_count == calls_after_refresh


@respx.mock
async def test_a_repeated_workout_is_only_parsed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concept2 re-runs workouts across years; identical text is free."""
    respx.route(url__regex=_URL_PATTERN).mock(return_value=Response(200, text=_page(_TITLE)))
    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None) -> Workout:
        nonlocal calls
        calls += 1
        return Workout(name="Parsed", mode=WorkoutMode.INTERVAL, source_text=text)

    monkeypatch.setattr("short_timer.concept2_cache.parse_workout_text", counting_parse)

    # Every cached day carries the same workout text, so one parse covers them all.
    await refresh_concept2_cache(force=True, today=date(2026, 7, 22))
    assert await ensure_wods_parsed() == 1
    assert calls == 1

    # A second pass has nothing left to do.
    assert await ensure_wods_parsed() == 0
    assert calls == 1

    shared = await find_parse(f"{_TITLE}\n\nSeven alternating intervals.")
    assert shared is not None and shared.mode == WorkoutMode.INTERVAL


@respx.mock
async def test_endpoint_marks_saved(authed_client: AsyncClient) -> None:
    respx.route(url__regex=_URL_PATTERN).mock(return_value=Response(200, text=_page(_TITLE)))
    text = f"{_TITLE}\n\nSeven alternating intervals."
    saved = await authed_client.post(
        "/api/workouts",
        json={
            "workout": Workout(name=_TITLE, mode=WorkoutMode.INTERVAL, source_text=text).model_dump(
                mode="json"
            )
        },
    )
    saved_id = saved.json()["id"]

    response = await authed_client.get("/api/concept2/wods?days=1")
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["title"] == _TITLE
    assert entries[0]["saved_workout_id"] == saved_id


@respx.mock
async def test_endpoint_unsaved_is_null(authed_client: AsyncClient) -> None:
    respx.route(url__regex=_URL_PATTERN).mock(return_value=Response(200, text=_page(_TITLE)))
    response = await authed_client.get("/api/concept2/wods?days=1")
    assert response.status_code == 200
    assert response.json()[0]["saved_workout_id"] is None


async def test_endpoint_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/concept2/wods")).status_code == 401


@respx.mock
async def test_existing_rows_are_not_rewritten() -> None:
    """A cached day is never refetched, so a paid-for parse can't be invalidated."""
    respx.route(url__regex=_URL_PATTERN).mock(return_value=Response(200, text=_page(_TITLE)))
    await refresh_concept2_cache(force=True, today=date(2026, 7, 22))
    before = await get_concept2_cache_collection().find_one({"_id": "2026-07-22"})
    assert before is not None

    respx.route(url__regex=_URL_PATTERN).mock(
        return_value=Response(200, text=_page("A different workout"))
    )
    assert await refresh_concept2_cache(force=True, today=date(2026, 7, 22)) == 0
    after = await get_concept2_cache_collection().find_one({"_id": "2026-07-22"})
    assert after is not None and after["title"] == _TITLE
