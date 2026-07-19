"""Mongo-backed cache of crossfit.com Workout of the Day pages.

crossfit.com publishes one workout per day, so there's no reason to fetch it
on demand. A background task refreshes the cache once a day and every request
reads from Mongo. That keeps our traffic to crossfit.com to a handful of
requests per day, survives restarts, and is shared across instances — none of
which was true of the previous in-process cache.

It also means the WOD tab keeps working when crossfit.com is unreachable: we
just serve the last good copy.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from short_timer.crossfit import Wod, fetch_recent_wods, is_rest_day
from short_timer.db import get_wod_cache_collection
from short_timer.dedup import source_hash
from short_timer.llm import parse_workout_text
from short_timer.models import Workout

logger = logging.getLogger(__name__)

#: Server-managed fields; a clone gets its own id, owner, and timestamps.
_PARSED_DROPPED_FIELDS = ("id", "created_at", "updated_at", "owner_id", "source_hash")

#: How many days back to keep cached and offer to the UI.
CACHE_DAYS = 14
#: Skip a refresh if the cache was updated more recently than this. Guards
#: against every instance re-fetching on a rolling deploy.
MIN_REFRESH_INTERVAL = timedelta(hours=12)
#: How long the background task sleeps between refreshes.
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60


def _to_document(wod: Wod) -> dict[str, object]:
    return {
        "_id": wod.date.isoformat(),
        "date": wod.date.isoformat(),
        "title": wod.title,
        "text": wod.text,
        "url": wod.url,
        "source_hash": source_hash(wod.text),
        "fetched_at": datetime.now(UTC),
    }


def _from_document(doc: dict[str, object]) -> Wod:
    return Wod(
        date=date.fromisoformat(str(doc["date"])),
        title=str(doc["title"]),
        text=str(doc["text"]),
        url=str(doc["url"]),
    )


async def last_refreshed_at() -> datetime | None:
    """When the cache was most recently written, if ever."""
    doc = await get_wod_cache_collection().find_one(sort=[("fetched_at", -1)])
    if doc is None:
        return None
    fetched = doc.get("fetched_at")
    if not isinstance(fetched, datetime):
        return None
    # Mongo returns naive UTC datetimes; make comparisons safe.
    return fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)


async def read_cached_wods(days: int) -> list[Wod]:
    """Most recent `days` cached WODs, newest first."""
    cursor = get_wod_cache_collection().find().sort("date", -1).limit(days)
    return [_from_document(doc) async for doc in cursor]


async def refresh_wod_cache(*, force: bool = False) -> int:
    """Fetch from crossfit.com and upsert into the cache. Returns rows written.

    Days that fail upstream are skipped rather than evicting a good cached
    copy, so a bad fetch degrades to stale data instead of an empty tab.
    """
    if not force:
        last = await last_refreshed_at()
        if last is not None and datetime.now(UTC) - last < MIN_REFRESH_INTERVAL:
            logger.debug("WOD cache still fresh; skipping refresh.")
            return 0

    wods = await fetch_recent_wods(CACHE_DAYS)
    if not wods:
        logger.warning("crossfit.com returned no workouts; keeping existing cache.")
        return 0

    collection = get_wod_cache_collection()
    for wod in wods:
        document = _to_document(wod)
        existing = await collection.find_one({"_id": document["_id"]}, {"text": 1})
        update: dict[str, object] = {"$set": document}
        # $set (rather than replace) keeps an existing parse. If crossfit.com
        # edited the day's text, that parse is stale — drop it to force a redo.
        if existing is not None and existing.get("text") != wod.text:
            update["$unset"] = {"parsed": ""}
        await collection.update_one({"_id": document["_id"]}, update, upsert=True)
    logger.info("Refreshed WOD cache with %d day(s).", len(wods))
    return len(wods)


async def ensure_wods_parsed(limit: int = CACHE_DAYS) -> int:
    """Parse each cached WOD once, so users clone it instead of re-parsing.

    A given day's workout gets added to many libraries. Parsing is per-owner
    (dedup is keyed on owner + text), so without this every user loading the
    same WOD would pay for an identical LLM call. Parsing once here makes that
    a single call regardless of how many people load it.
    """
    collection = get_wod_cache_collection()
    parsed = 0
    async for doc in collection.find({"parsed": None}).sort("date", -1).limit(limit):
        text = str(doc.get("text") or "")
        # Rest days have nothing to time, and the UI won't offer to load them.
        if not text or is_rest_day(text):
            continue
        title = str(doc.get("title") or "") or None
        try:
            workout = await parse_workout_text(text, name_hint=title)
        except Exception:  # noqa: BLE001 - one bad day shouldn't stop the rest
            logger.exception("Could not pre-parse WOD %s", doc.get("date"))
            continue

        payload = workout.model_dump(mode="json")
        for field in _PARSED_DROPPED_FIELDS:
            payload.pop(field, None)
        await collection.update_one({"_id": doc["_id"]}, {"$set": {"parsed": payload}})
        parsed += 1

    if parsed:
        logger.info("Pre-parsed %d WOD(s).", parsed)
    return parsed


async def backfill_wod_source_hashes() -> int:
    """Populate `source_hash` on cache rows written before the field existed.

    The shared-parse lookup keys on this hash, so a missing value makes it
    silently miss — every user then pays for their own parse even though a
    pre-parsed copy is sitting right there. The refresh only rewrites rows
    when it actually re-fetches, so stale rows need fixing up directly.
    """
    collection = get_wod_cache_collection()
    updated = 0
    async for doc in collection.find({"source_hash": None}):
        text = str(doc.get("text") or "")
        if not text:
            continue
        await collection.update_one(
            {"_id": doc["_id"]}, {"$set": {"source_hash": source_hash(text)}}
        )
        updated += 1
    return updated


async def find_parsed_workout(text: str) -> Workout | None:
    """A shared pre-parsed WOD matching this text, as a fresh unsaved Workout."""
    doc = await get_wod_cache_collection().find_one(
        {"source_hash": source_hash(text), "parsed": {"$ne": None}}
    )
    if doc is None:
        return None
    parsed = doc.get("parsed")
    if not isinstance(parsed, dict):
        return None
    # Rebuilt through the model so the clone gets its own id and timestamps.
    return Workout(**dict(parsed))


async def get_wods(days: int) -> list[Wod]:
    """Cached WODs, fetching once on demand if the cache has never been filled."""
    cached = await read_cached_wods(days)
    if cached:
        return cached
    # Cold cache (first boot, or fresh database) — populate it inline so the
    # first visitor sees data rather than an empty tab.
    await refresh_wod_cache(force=True)
    return await read_cached_wods(days)
