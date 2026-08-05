"""MongoDB access via PyMongo's native async client.

Motor is no longer maintained now that PyMongo ships its own asyncio
support (`pymongo.AsyncMongoClient`, 4.9+); this talks to Mongo directly
through that instead.
"""

import inspect
import logging
from collections.abc import AsyncIterable
from functools import lru_cache
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from short_timer.config import get_settings
from short_timer.dedup import source_hash

logger = logging.getLogger(__name__)


@lru_cache
def get_client() -> AsyncMongoClient[dict[str, Any]]:
    settings = get_settings()
    return AsyncMongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        connectTimeoutMS=settings.mongodb_timeout_ms,
    )


def get_database() -> AsyncDatabase[dict[str, Any]]:
    return get_client()[get_settings().mongodb_db_name]


def get_workouts_collection() -> AsyncCollection[dict[str, Any]]:
    return get_database()["workouts"]


def get_wod_cache_collection() -> AsyncCollection[dict[str, Any]]:
    """Cached crossfit.com Workout of the Day pages, keyed by date."""
    return get_database()["wod_cache"]


def get_concept2_cache_collection() -> AsyncCollection[dict[str, Any]]:
    """Cached Concept2 erg Workouts of the Day, keyed by date."""
    return get_database()["concept2_cache"]


def get_hybrid_cache_collection() -> AsyncCollection[dict[str, Any]]:
    """The Hybrid Calisthenics weekly rotation — a single document, not dated rows."""
    return get_database()["hybrid_cache"]


def get_parse_cache_collection() -> AsyncCollection[dict[str, Any]]:
    """Shared pool of parsed workouts, keyed by source-text hash (`_id`)."""
    return get_database()["parse_cache"]


def get_gym_cache_collection() -> AsyncCollection[dict[str, Any]]:
    """Cached gym workouts, keyed by gym fingerprint + date (see gym_cache).

    Renamed from `wodify_cache` when gyms stopped being Wodify-only. Nothing
    migrates the old collection: it holds only derived data with a 12-hour
    refresh interval, so the first request for a gym repopulates it and the
    stale collection can simply be dropped.
    """
    return get_database()["gym_cache"]


def get_users_collection() -> AsyncCollection[dict[str, Any]]:
    """Accounts, keyed by user id (`_id`), which is also their `owner_id`."""
    return get_database()["users"]


def get_sessions_collection() -> AsyncCollection[dict[str, Any]]:
    """Live sessions, keyed by the SHA-256 of the token (see sessions.py).

    The token itself is never stored, so a dump of this collection can't be
    replayed against the API.
    """
    return get_database()["sessions"]


def get_rate_limit_collection() -> AsyncCollection[dict[str, Any]]:
    """Rate-limit counters, one document per (scope, subject, window)."""
    return get_database()["rate_limits"]


def get_events_collection() -> AsyncCollection[dict[str, Any]]:
    """What the app did, one document per event (see metrics.py)."""
    return get_database()["events"]


async def aggregate(
    collection: AsyncCollection[dict[str, Any]], pipeline: list[dict[str, Any]]
) -> AsyncIterable[dict[str, Any]]:
    """Run an aggregation, tolerating both async-cursor conventions.

    PyMongo's async client returns a *coroutine* that yields the cursor, while
    the mongomock double the tests run against returns the cursor directly —
    so `await collection.aggregate(...)` is correct in production and a
    TypeError under test, and dropping the `await` is the reverse.

    Normalising here means call sites are written once and neither convention
    leaks into them. It's a seam that exists purely because the test double is
    imperfect, which is worth saying out loud rather than dressing up.
    """
    cursor: Any = collection.aggregate(pipeline)
    if inspect.isawaitable(cursor):
        cursor = await cursor
    return cursor  # type: ignore[no-any-return]


async def ensure_indexes() -> None:
    """Index the fields every hot query filters on."""
    # Dedup lookups are always scoped to an owner, so index the pair.
    await get_workouts_collection().create_index([("owner_id", 1), ("source_hash", 1)])
    await get_workouts_collection().create_index("owner_id")
    # The library lists one owner's workouts newest-first, a page at a time, so
    # the sort should come off the index rather than a per-request sort of the
    # whole library.
    await get_workouts_collection().create_index([("owner_id", 1), ("created_at", -1)])
    await get_wod_cache_collection().create_index("date")
    await get_concept2_cache_collection().create_index("date")
    # The gym feed always reads one gym's recent days, so index the pair.
    await get_gym_cache_collection().create_index([("gym", 1), ("date", -1)])
    # parse_cache is keyed by source hash as its _id, so lookups need no index.
    # The retention sweep filters on provenance and age, though.
    await get_parse_cache_collection().create_index([("source", 1), ("created_at", 1)])
    # Spent rate-limit windows clean themselves up rather than growing forever.
    await get_rate_limit_collection().create_index("expires_at", expireAfterSeconds=0)
    # One account per address. Sparse, because the shared-passcode account has
    # no email and two documents with a missing field would otherwise collide
    # on a plain unique index.
    await get_users_collection().create_index("email", unique=True, sparse=True)
    # "End every session for this user" is a delete by user_id, and it runs on
    # every password reset.
    await get_sessions_collection().create_index("user_id")
    # Expired sessions are already rejected and deleted on read (see
    # sessions.py). This index is only the janitor for tokens nobody ever
    # presents again — it is not the expiry check.
    await get_sessions_collection().create_index("expires_at", expireAfterSeconds=0)
    # Every metrics question is "this type, over this window", optionally for
    # one owner — so the compound index leads with the two that always appear.
    await get_events_collection().create_index([("type", 1), ("at", -1)])
    await get_events_collection().create_index([("owner_id", 1), ("at", -1)])
    # Raw events age out on their own. Aggregating into rollups first would be
    # premature at this volume; the retention window is the knob to reach for
    # if that changes (see `events_retention_days`).
    await get_events_collection().create_index(
        "at", expireAfterSeconds=get_settings().events_retention_days * 24 * 60 * 60
    )


async def backfill_source_hashes() -> int:
    """Populate `source_hash` on workouts saved before the field existed.

    Without this, legacy rows are invisible to dedup lookups: they'd be
    duplicated on save and re-parsed by the LLM even though an identical
    workout is already stored.
    """
    collection = get_workouts_collection()
    updated = 0
    async for doc in collection.find({"source_hash": None}):
        text = doc.get("source_text")
        if not text:
            continue
        await collection.update_one(
            {"_id": doc["_id"]}, {"$set": {"source_hash": source_hash(text)}}
        )
        updated += 1
    return updated


async def backfill_owner_ids(default_owner_id: str) -> int:
    """Assign pre-tenancy workouts to the default owner.

    Rows written before `owner_id` existed would otherwise be invisible to
    every owner-scoped query — effectively vanishing from the library.

    The owner is passed in rather than imported: this module sits *below*
    `auth`, which now reaches the database itself to resolve sessions, so
    importing the constant from there would close an import cycle.
    """
    result = await get_workouts_collection().update_many(
        {"owner_id": None}, {"$set": {"owner_id": default_owner_id}}
    )
    return int(result.modified_count)
