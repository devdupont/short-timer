from datetime import date

import httpx
import pytest
import respx
from httpx import AsyncClient, Response

from shortimer.cache.crossfit import (
    CACHE_DAYS,
    ensure_wods_parsed,
    get_wods,
    read_cached_wods,
    refresh_wod_cache,
)
from shortimer.cache.parse import find_parse
from shortimer.model.workout import Workout, WorkoutMode
from shortimer.service import crossfit
from shortimer.service.crossfit import fetch_wod

_WOD_JSON = {
    "wods": {
        "cleanID": "20260718",
        "title": "Saturday 260718",
        "wodRaw": "50-40-30-20-10 reps for time of:\nDouble-unders\nSit-ups",
    }
}


@respx.mock
async def test_wods_are_served_from_cache_without_refetching() -> None:
    """The daily refresh is the only thing that should hit crossfit.com."""
    route = respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    assert await refresh_wod_cache(force=True) > 0
    calls_after_refresh = route.call_count

    # Reads come out of Mongo, so no further upstream requests.
    assert len(await get_wods(3)) > 0
    assert len(await get_wods(3)) > 0
    assert route.call_count == calls_after_refresh


@respx.mock
async def test_refresh_keeps_stale_cache_when_upstream_fails() -> None:
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    assert await refresh_wod_cache(force=True) > 0
    cached = await read_cached_wods(CACHE_DAYS)

    # crossfit.com goes down: keep serving what we already have.
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(502)
    )
    assert await refresh_wod_cache(force=True) == 0
    assert len(await read_cached_wods(CACHE_DAYS)) == len(cached)


@respx.mock
async def test_wods_are_parsed_once_and_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """One parse per WOD, however many libraries it ends up in."""
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        nonlocal calls
        calls += 1
        return Workout(name="Parsed", mode=WorkoutMode.FOR_TIME, source_text=text)

    monkeypatch.setattr("shortimer.cache.crossfit.parse_workout_text", counting_parse)

    await refresh_wod_cache(force=True)
    assert await ensure_wods_parsed() > 0
    parses_after_prewarm = calls

    # A second pass has nothing left to do.
    assert await ensure_wods_parsed() == 0
    assert calls == parses_after_prewarm

    # And the shared parse is available to clone, with its own fresh id.
    shared = await find_parse(_WOD_JSON["wods"]["wodRaw"])
    assert shared is not None
    again = await find_parse(_WOD_JSON["wods"]["wodRaw"])
    assert again is not None and again.id != shared.id
    assert calls == parses_after_prewarm


@respx.mock
async def test_rest_days_are_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(
            200, json={"wods": {"title": "Rest", "wodRaw": "**Rest Day**\n\nA hero story."}}
        )
    )

    async def exploding_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        raise AssertionError("rest days have no workout to parse")

    monkeypatch.setattr("shortimer.cache.crossfit.parse_workout_text", exploding_parse)

    await refresh_wod_cache(force=True)
    assert await ensure_wods_parsed() == 0


@respx.mock
async def test_refetch_preserves_parse_but_stale_text_invalidates_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        nonlocal calls
        calls += 1
        return Workout(name="Parsed", mode=WorkoutMode.FOR_TIME, source_text=text)

    monkeypatch.setattr("shortimer.cache.crossfit.parse_workout_text", counting_parse)

    await refresh_wod_cache(force=True)
    await ensure_wods_parsed()
    first_pass = calls

    # Re-fetching identical text must not throw away the existing parses.
    await refresh_wod_cache(force=True)
    assert await ensure_wods_parsed() == 0
    assert calls == first_pass

    # If crossfit.com edits the text, the old parse is stale and must be redone.
    edited = {"wods": {**_WOD_JSON["wods"], "wodRaw": "21-15-9 reps for time of:\nThrusters"}}
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=edited)
    )
    await refresh_wod_cache(force=True)
    assert await ensure_wods_parsed() > 0
    assert calls > first_pass


@respx.mock
async def test_refresh_skips_when_cache_is_fresh() -> None:
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    assert await refresh_wod_cache(force=True) > 0
    # An unforced refresh right after should no-op rather than refetch.
    assert await refresh_wod_cache() == 0


@respx.mock
async def test_fetch_wod_parses_json() -> None:
    respx.get("https://www.crossfit.com/workout/2026/07/18").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    async with httpx.AsyncClient() as http:
        wod = await fetch_wod(http, date(2026, 7, 18))
    assert wod is not None
    assert wod.title == "Saturday 260718"
    assert "Double-unders" in wod.text
    assert wod.url == "https://www.crossfit.com/260718"


@respx.mock
async def test_fetch_wod_skips_server_error() -> None:
    respx.get("https://www.crossfit.com/workout/2026/07/12").mock(return_value=Response(502))
    async with httpx.AsyncClient() as http:
        assert await fetch_wod(http, date(2026, 7, 12)) is None


@respx.mock
async def test_fetch_wod_skips_empty_body() -> None:
    respx.get("https://www.crossfit.com/workout/2026/07/25").mock(
        return_value=Response(200, json={"wods": {"title": "Future", "wodRaw": ""}})
    )
    async with httpx.AsyncClient() as http:
        assert await fetch_wod(http, date(2026, 7, 25)) is None


@respx.mock
async def test_wods_endpoint_marks_saved(authed_client: AsyncClient) -> None:
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    # Save a workout whose source text matches the WOD so it's flagged as saved.
    saved = await authed_client.post(
        "/api/workouts",
        json={
            "workout": Workout(
                name="Saturday 260718",
                mode=WorkoutMode.FOR_TIME,
                source_text=_WOD_JSON["wods"]["wodRaw"],
            ).model_dump(mode="json")
        },
    )
    saved_id = saved.json()["id"]

    response = await authed_client.get("/api/wods?days=1")
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["title"] == "Saturday 260718"
    assert entries[0]["saved_workout_id"] == saved_id


@respx.mock
async def test_wods_endpoint_unsaved_is_null(authed_client: AsyncClient) -> None:
    respx.route(url__regex=r"https://www\.crossfit\.com/workout/.*").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    response = await authed_client.get("/api/wods?days=1")
    assert response.status_code == 200
    assert response.json()[0]["saved_workout_id"] is None


async def test_wods_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/wods")).status_code == 401


@respx.mock
async def test_fetch_recent_wods_skips_failures() -> None:
    respx.get("https://www.crossfit.com/workout/2026/07/18").mock(
        return_value=Response(200, json=_WOD_JSON)
    )
    respx.get("https://www.crossfit.com/workout/2026/07/17").mock(return_value=Response(502))
    wods = await crossfit.fetch_recent_wods(2, today=date(2026, 7, 18))
    assert [w.date.isoformat() for w in wods] == ["2026-07-18"]
