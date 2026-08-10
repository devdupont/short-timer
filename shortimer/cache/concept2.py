"""Mongo-backed cache of Concept2's daily erg Workout of the Day.

Same contract as `wod_cache`: a background task keeps it warm, every request
reads from Mongo, and a failed fetch degrades to stale data rather than an
empty feed.

It differs in how it *refreshes*, and deliberately so. `wod_cache` re-fetches
the whole window each day; this one fetches only the days it's missing. In
steady state that's a single request for today, because Concept2 doesn't edit a
day once it's published.

That's the shape a source has to have when it only publishes "today" with no
date in the URL: you can never fetch a day you missed, so the cache *is* the
history and every refresh can only ever append. Concept2 happens to be
date-addressable — `fetch_days` will fill a gap left by a few days of downtime,
which a today-only source couldn't — but nothing else here depends on that. A
feed that can only answer for today drops in by having `_missing_days` return
at most today.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from shortimer.cache.db import get_concept2_cache_collection
from shortimer.cache.parse import (
    SOURCE_CONCEPT2,
    find_parse,
    mark_permanent,
    remember_parse,
)
from shortimer.service.concept2 import Concept2Wod, fetch_days
from shortimer.service.llm import parse_workout_text

logger = logging.getLogger(__name__)

#: How many days back to keep cached and offer to the UI.
CACHE_DAYS = 14
#: Skip a refresh if the cache was updated more recently than this. Guards
#: against every instance re-fetching on a rolling deploy.
MIN_REFRESH_INTERVAL = timedelta(hours=12)
#: How long the background task sleeps between refreshes.
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60


def _to_document(wod: Concept2Wod) -> dict[str, object]:
    return {
        "_id": wod.date.isoformat(),
        "date": wod.date.isoformat(),
        "title": wod.title,
        "text": wod.text,
        "url": wod.url,
        "fetched_at": datetime.now(UTC),
    }


def _from_document(doc: dict[str, object]) -> Concept2Wod:
    return Concept2Wod(
        date=date.fromisoformat(str(doc["date"])),
        title=str(doc["title"]),
        text=str(doc["text"]),
        url=str(doc["url"]),
    )


async def last_refreshed_at() -> datetime | None:
    """When the cache was most recently written, if ever."""
    doc = await get_concept2_cache_collection().find_one(sort=[("fetched_at", -1)])
    if doc is None:
        return None
    fetched = doc.get("fetched_at")
    if not isinstance(fetched, datetime):
        return None
    # Mongo returns naive UTC datetimes; make comparisons safe.
    return fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)


async def read_cached_wods(days: int) -> list[Concept2Wod]:
    """Most recent `days` cached workouts, newest first."""
    cursor = get_concept2_cache_collection().find().sort("date", -1).limit(days)
    return [_from_document(doc) async for doc in cursor]


async def _missing_days(anchor: date) -> list[date]:
    """Days in the window we have no row for, newest first.

    On a cold cache that's the whole window; on a warm one it's just today.
    """
    window = [anchor - timedelta(days=offset) for offset in range(CACHE_DAYS)]
    collection = get_concept2_cache_collection()
    cached = {
        str(doc["_id"])
        async for doc in collection.find(
            {"_id": {"$in": [day.isoformat() for day in window]}}, {"_id": 1}
        )
    }
    return [day for day in window if day.isoformat() not in cached]


async def refresh_concept2_cache(*, force: bool = False, today: date | None = None) -> int:
    """Fetch missing days from Concept2 and insert them. Returns rows written.

    Only days we don't already hold are fetched, so a normal day costs one
    request. Existing rows are never rewritten: Concept2 doesn't revise a
    published day, and leaving them alone means a re-fetch can't invalidate a
    parse that's already been paid for.
    """
    if not force:
        last = await last_refreshed_at()
        if last is not None and datetime.now(UTC) - last < MIN_REFRESH_INTERVAL:
            logger.debug("Concept2 cache still fresh; skipping refresh.")
            return 0

    anchor = today or datetime.now().date()
    missing = await _missing_days(anchor)
    if not missing:
        return 0

    wods = await fetch_days(missing)
    if not wods:
        logger.warning("Concept2 returned no workouts; keeping existing cache.")
        return 0

    collection = get_concept2_cache_collection()
    for wod in wods:
        document = _to_document(wod)
        await collection.replace_one({"_id": document["_id"]}, document, upsert=True)
    logger.info("Cached %d new Concept2 workout(s).", len(wods))
    return len(wods)


async def ensure_wods_parsed(limit: int = CACHE_DAYS) -> int:
    """Warm the shared parse pool with each cached day, ahead of any user.

    Same reasoning as the crossfit.com pre-parse: parsing is deduped on text,
    so doing it here means one model call per distinct workout rather than one
    per user who opens it.

    Concept2 gets more out of this than crossfit.com does, because it reuses
    its workouts — "12 x 250m / 45 sec easy" ran on 2024-07-12 and again on
    2026-07-12. Identical text hashes to the same pool entry, so a repeat costs
    nothing at all.
    """
    parsed = 0
    async for doc in get_concept2_cache_collection().find().sort("date", -1).limit(limit):
        text = str(doc.get("text") or "")
        if not text:
            continue
        if await find_parse(text) is not None:
            # Already in the pool — either a previous day with the same
            # workout, or a user who pasted it first. Either way it's
            # Concept2's text, so keep it off the user retention clock.
            await mark_permanent(text, source=SOURCE_CONCEPT2)
            continue
        title = str(doc.get("title") or "") or None
        try:
            workout = await parse_workout_text(text, name_hint=title, purpose="prewarm:concept2")
        except Exception:  # one bad day shouldn't stop the rest
            logger.exception("Could not pre-parse Concept2 WOD %s", doc.get("date"))
            continue
        await remember_parse(workout, source=SOURCE_CONCEPT2)
        parsed += 1

    if parsed:
        logger.info("Pre-parsed %d Concept2 workout(s).", parsed)
    return parsed


async def get_wods(days: int) -> list[Concept2Wod]:
    """Cached workouts, fetching once on demand if the cache is empty."""
    cached = await read_cached_wods(days)
    if cached:
        return cached
    # Cold cache (first boot, or fresh database) — populate it inline so the
    # first visitor sees data rather than an empty feed.
    await refresh_concept2_cache(force=True)
    return await read_cached_wods(days)
