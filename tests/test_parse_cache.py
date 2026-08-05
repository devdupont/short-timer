from datetime import UTC, datetime, timedelta

from short_timer.db import get_parse_cache_collection
from short_timer.dedup import source_hash
from short_timer.models import Workout, WorkoutMode
from short_timer.parse_cache import (
    SOURCE_CROSSFIT,
    SOURCE_USER,
    USER_RETENTION,
    find_parse,
    mark_permanent,
    prune_expired_parses,
    remember_parse,
)

CROSSFIT_TEXT = "50-40-30-20-10 reps for time of:\nDouble-unders\nSit-ups"
USER_TEXT = "Coach's whiteboard special\n3 rounds:\n10 burpees"


def _workout(text: str) -> Workout:
    return Workout(name="Probe", mode=WorkoutMode.FOR_TIME, source_text=text)


async def _age_entry(text: str, age: timedelta) -> None:
    await get_parse_cache_collection().update_one(
        {"_id": source_hash(text)}, {"$set": {"created_at": datetime.now(UTC) - age}}
    )


async def test_user_parses_expire_but_crossfit_ones_do_not() -> None:
    await remember_parse(_workout(CROSSFIT_TEXT), source=SOURCE_CROSSFIT)
    await remember_parse(_workout(USER_TEXT), source=SOURCE_USER)

    # Both well past the retention window.
    await _age_entry(CROSSFIT_TEXT, USER_RETENTION + timedelta(days=30))
    await _age_entry(USER_TEXT, USER_RETENTION + timedelta(days=30))

    assert await prune_expired_parses() == 1
    assert await find_parse(CROSSFIT_TEXT) is not None
    assert await find_parse(USER_TEXT) is None


async def test_recent_user_parses_are_kept() -> None:
    await remember_parse(_workout(USER_TEXT), source=SOURCE_USER)
    await _age_entry(USER_TEXT, USER_RETENTION - timedelta(days=1))

    assert await prune_expired_parses() == 0
    assert await find_parse(USER_TEXT) is not None


async def test_a_user_parse_of_a_wod_gets_promoted_to_permanent() -> None:
    """Pasting a day before the daily task reaches it shouldn't start a clock."""
    await remember_parse(_workout(CROSSFIT_TEXT), source=SOURCE_USER)
    assert await mark_permanent(CROSSFIT_TEXT) is True

    await _age_entry(CROSSFIT_TEXT, USER_RETENTION + timedelta(days=30))
    assert await prune_expired_parses() == 0
    assert await find_parse(CROSSFIT_TEXT) is not None


async def test_user_parse_never_demotes_a_permanent_entry() -> None:
    await remember_parse(_workout(CROSSFIT_TEXT), source=SOURCE_CROSSFIT)
    # A user pasting the same text re-records it; provenance must survive.
    await remember_parse(_workout(CROSSFIT_TEXT), source=SOURCE_USER)

    doc = await get_parse_cache_collection().find_one({"_id": source_hash(CROSSFIT_TEXT)})
    assert doc is not None and doc["source"] == SOURCE_CROSSFIT
