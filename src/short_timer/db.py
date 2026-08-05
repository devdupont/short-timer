"""MongoDB access via PyMongo's native async client.

Motor is no longer maintained now that PyMongo ships its own asyncio
support (`pymongo.AsyncMongoClient`, 4.9+); this talks to Mongo directly
through that instead.
"""

import logging
from functools import lru_cache
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from short_timer.auth import DEFAULT_OWNER_ID
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


def get_rate_limit_collection() -> AsyncCollection[dict[str, Any]]:
    """Rate-limit counters, one document per (scope, subject, window)."""
    return get_database()["rate_limits"]


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


async def backfill_owner_ids() -> int:
    """Assign pre-tenancy workouts to the default owner.

    Rows written before `owner_id` existed would otherwise be invisible to
    every owner-scoped query — effectively vanishing from the library.
    """
    result = await get_workouts_collection().update_many(
        {"owner_id": None}, {"$set": {"owner_id": DEFAULT_OWNER_ID}}
    )
    return int(result.modified_count)
