from datetime import UTC, datetime, timedelta

from short_timer.db import get_parse_cache_collection, get_wod_cache_collection
from short_timer.dedup import source_hash
from short_timer.models import Workout, WorkoutMode
from short_timer.parse_cache import (
    SOURCE_CROSSFIT,
    SOURCE_USER,
    USER_RETENTION,
    backfill_parse_sources,
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


async def test_backfill_labels_entries_by_whether_they_match_a_cached_wod() -> None:
    """Unlabelled entries would otherwise all be swept as user content."""
    await get_wod_cache_collection().insert_one(
        {"_id": "2026-07-18", "date": "2026-07-18", "text": CROSSFIT_TEXT, "title": "A WOD"}
    )
    for text in (CROSSFIT_TEXT, USER_TEXT):
        await get_parse_cache_collection().insert_one(
            {
                "_id": source_hash(text),
                "source_hash": source_hash(text),
                "parsed": {"name": "Legacy", "mode": "for_time", "segments": []},
            }
        )

    assert await backfill_parse_sources() == 2

    collection = get_parse_cache_collection()
    from_wod = await collection.find_one({"_id": source_hash(CROSSFIT_TEXT)})
    from_user = await collection.find_one({"_id": source_hash(USER_TEXT)})
    assert from_wod is not None and from_wod["source"] == SOURCE_CROSSFIT
    assert from_user is not None and from_user["source"] == SOURCE_USER
    # A timestamp is required for the sweep to age anything out.
    assert isinstance(from_user["created_at"], datetime)

    # Idempotent.
    assert await backfill_parse_sources() == 0
