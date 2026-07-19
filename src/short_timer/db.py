"""MongoDB access via PyMongo's native async client.

Motor is no longer maintained now that PyMongo ships its own asyncio
support (`pymongo.AsyncMongoClient`, 4.9+); this talks to Mongo directly
through that instead.
"""

from functools import lru_cache
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from short_timer.auth import DEFAULT_OWNER_ID
from short_timer.config import get_settings
from short_timer.dedup import source_hash


@lru_cache
def get_client() -> AsyncMongoClient[dict[str, Any]]:
    return AsyncMongoClient(get_settings().mongodb_uri)


def get_database() -> AsyncDatabase[dict[str, Any]]:
    return get_client()[get_settings().mongodb_db_name]


def get_workouts_collection() -> AsyncCollection[dict[str, Any]]:
    return get_database()["workouts"]


def get_wod_cache_collection() -> AsyncCollection[dict[str, Any]]:
    """Cached crossfit.com Workout of the Day pages, keyed by date."""
    return get_database()["wod_cache"]


async def ensure_indexes() -> None:
    """Index the fields every hot query filters on."""
    # Dedup lookups are always scoped to an owner, so index the pair.
    await get_workouts_collection().create_index([("owner_id", 1), ("source_hash", 1)])
    await get_workouts_collection().create_index("owner_id")
    await get_wod_cache_collection().create_index("date")


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
