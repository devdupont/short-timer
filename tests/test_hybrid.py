from datetime import date

import pytest
import respx
from httpx import AsyncClient, Response

from short_timer.db import get_hybrid_cache_collection
from short_timer.hybrid import PAGE_URL, is_rest_day, parse_rotation
from short_timer.hybrid_cache import (
    ensure_wods_parsed,
    get_wods,
    read_cached_rotation,
    refresh_hybrid_cache,
)
from short_timer.models import Workout, WorkoutMode
from short_timer.parse_cache import find_parse


def _day_block(day: str, *exercises: str) -> str:
    """One Squarespace content block, shaped like the real page."""
    lines = "".join(f'<p class="sqsrte-large">{e}</p>' for e in exercises)
    return (
        f'<div class="sqs-html-content"><h1>{day}</h1>{lines}'
        '<p><strong>Variations:</strong> <a href="/wall-pushups">Wall Pushups</a></p></div>'
    )


#: The real page repeats three workouts over six days, then rests.
_PAGE = (
    "<html><body>"
    + "".join(
        [
            _day_block("Monday", "Pushups (2-3 Sets)", "Leg Raises (2-3 Sets)"),
            _day_block("Tuesday", "Pullups (2-3 Sets)", "Squats (2-3 Sets)"),
            _day_block("Wednesday", "Bridges (2-3 Sets)", "Twists (2-3 Sets)"),
            _day_block("Thursday", "Pushups (2-3 Sets)", "Leg Raises (2-3 Sets)"),
            _day_block("Friday", "Pullups (2-3 Sets)", "Squats (2-3 Sets)"),
            _day_block("Saturday", "Bridges (2-3 Sets)", "Twists (2-3 Sets)"),
            _day_block("Sunday", "A Day of Rest"),
        ]
    )
    + "</body></html>"
)


def test_parse_reads_the_whole_rotation() -> None:
    rotation = parse_rotation(_PAGE)
    assert set(rotation.days) == {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }
    assert rotation.days["Monday"] == ["Pushups (2-3 Sets)", "Leg Raises (2-3 Sets)"]


def test_parse_ignores_variation_ladders() -> None:
    """Progressions are reference material, not the day's workout."""
    rotation = parse_rotation(_PAGE)
    assert all("Wall Pushups" not in line for line in rotation.days["Monday"])


def test_parse_of_an_unrecognisable_page_is_empty() -> None:
    """So a markup change can't overwrite a good cached rotation with junk."""
    assert parse_rotation("<html><body><h1>Something else</h1></body></html>").days == {}


def test_projection_maps_weekday_to_workout() -> None:
    rotation = parse_rotation(_PAGE)
    # 2026-07-22 is a Wednesday; 07-20 a Monday; 07-19 a Sunday.
    assert rotation.for_date(date(2026, 7, 22)).title == "Bridges & Twists"
    assert rotation.for_date(date(2026, 7, 20)).title == "Pushups & Leg Raises"
    assert rotation.for_date(date(2026, 7, 19)).title == "A Day of Rest"


def test_title_drops_the_set_count() -> None:
    """The card heading should read as a workout name, not a prescription."""
    rotation = parse_rotation(_PAGE)
    assert rotation.for_date(date(2026, 7, 21)).title == "Pullups & Squats"


def test_rest_day_is_recognised() -> None:
    assert is_rest_day("A Day of Rest")
    assert not is_rest_day("Pushups (2-3 Sets)")


@respx.mock
async def test_refresh_stores_one_document_not_dated_rows() -> None:
    respx.get(PAGE_URL).mock(return_value=Response(200, text=_PAGE))
    assert await refresh_hybrid_cache(force=True) is True

    assert await get_hybrid_cache_collection().count_documents({}) == 1
    rotation = await read_cached_rotation()
    assert rotation is not None and len(rotation.days) == 7


@respx.mock
async def test_refresh_keeps_the_cached_rotation_when_upstream_fails() -> None:
    respx.get(PAGE_URL).mock(return_value=Response(200, text=_PAGE))
    await refresh_hybrid_cache(force=True)

    respx.get(PAGE_URL).mock(return_value=Response(503))
    assert await refresh_hybrid_cache(force=True) is False
    rotation = await read_cached_rotation()
    assert rotation is not None and len(rotation.days) == 7


@respx.mock
async def test_refresh_keeps_the_cached_rotation_when_markup_moves() -> None:
    """A page we can fetch but not understand must not clobber a good cache."""
    respx.get(PAGE_URL).mock(return_value=Response(200, text=_PAGE))
    await refresh_hybrid_cache(force=True)

    respx.get(PAGE_URL).mock(return_value=Response(200, text="<html><body>redesign</body></html>"))
    assert await refresh_hybrid_cache(force=True) is False
    rotation = await read_cached_rotation()
    assert rotation is not None and len(rotation.days) == 7


@respx.mock
async def test_refresh_skips_when_the_rotation_is_fresh() -> None:
    route = respx.get(PAGE_URL).mock(return_value=Response(200, text=_PAGE))
    assert await refresh_hybrid_cache(force=True) is True
    calls = route.call_count

    # A rotation changes about never, so an unforced refresh shouldn't refetch.
    assert await refresh_hybrid_cache() is False
    assert route.call_count == calls


@respx.mock
async def test_history_comes_from_projection_without_dated_rows() -> None:
    """Two weeks of history from a single stored document."""
    respx.get(PAGE_URL).mock(return_value=Response(200, text=_PAGE))
    await refresh_hybrid_cache(force=True)

    wods = await get_wods(14, today=date(2026, 7, 22))
    assert len(wods) == 14
    assert [w.date.isoformat() for w in wods[:3]] == ["2026-07-22", "2026-07-21", "2026-07-20"]
    assert await get_hybrid_cache_collection().count_documents({}) == 1


@respx.mock
async def test_repeats_and_rest_days_cost_no_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Six days over three workouts is three parses, and rest days are skipped."""
    respx.get(PAGE_URL).mock(return_value=Response(200, text=_PAGE))
    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        nonlocal calls
        calls += 1
        return Workout(name="Parsed", mode=WorkoutMode.CUSTOM, source_text=text)

    monkeypatch.setattr("short_timer.hybrid_cache.parse_workout_text", counting_parse)

    await refresh_hybrid_cache(force=True)
    assert await ensure_wods_parsed() == 3
    assert calls == 3

    # A second pass has nothing left to do.
    assert await ensure_wods_parsed() == 0
    assert calls == 3

    assert await find_parse("Pushups (2-3 Sets)\nLeg Raises (2-3 Sets)") is not None
    # The rest day was never sent to the model.
    assert await find_parse("A Day of Rest") is None


@respx.mock
async def test_endpoint_returns_the_rotation(authed_client: AsyncClient) -> None:
    respx.get(PAGE_URL).mock(return_value=Response(200, text=_PAGE))
    response = await authed_client.get("/api/hybrid/wods?days=3")
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 3
    assert entries[0]["url"] == PAGE_URL
    assert entries[0]["saved_workout_id"] is None


async def test_endpoint_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/hybrid/wods")).status_code == 401
