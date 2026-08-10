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

from shortimer.cache.db import get_wod_cache_collection
from shortimer.cache.parse import (
    SOURCE_CROSSFIT,
    find_parse,
    mark_permanent,
    remember_parse,
)
from shortimer.service.crossfit import Wod, fetch_recent_wods, is_rest_day
from shortimer.service.llm import parse_workout_text

logger = logging.getLogger(__name__)

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
        await collection.replace_one({"_id": document["_id"]}, document, upsert=True)
    logger.info("Refreshed WOD cache with %d day(s).", len(wods))
    return len(wods)


async def ensure_wods_parsed(limit: int = CACHE_DAYS) -> int:
    """Warm the shared parse pool with each cached day, ahead of any user.

    A given day's workout gets added to many libraries. Parsing is per-owner
    (dedup is keyed on owner + text), so without this the first user to load a
    day would pay for the parse. Doing it here means the model is called once
    per day regardless of how many people load it — and the result lands in
    the same pool that pasted workouts use.
    """
    parsed = 0
    async for doc in get_wod_cache_collection().find().sort("date", -1).limit(limit):
        text = str(doc.get("text") or "")
        # Rest days have nothing to time, and the UI won't offer to load them.
        if not text or is_rest_day(text):
            continue
        if await find_parse(text) is not None:
            # Someone pasted this day before we got to it — it's still
            # crossfit.com's text, so keep it permanently rather than letting
            # it age out on the user retention clock.
            await mark_permanent(text)
            continue
        title = str(doc.get("title") or "") or None
        try:
            workout = await parse_workout_text(text, name_hint=title, purpose="prewarm:crossfit")
        except Exception:  # one bad day shouldn't stop the rest
            logger.exception("Could not pre-parse WOD %s", doc.get("date"))
            continue
        await remember_parse(workout, source=SOURCE_CROSSFIT)
        parsed += 1

    if parsed:
        logger.info("Pre-parsed %d WOD(s).", parsed)
    return parsed


async def get_wods(days: int) -> list[Wod]:
    """Cached WODs, fetching once on demand if the cache has never been filled."""
    cached = await read_cached_wods(days)
    if cached:
        return cached
    # Cold cache (first boot, or fresh database) — populate it inline so the
    # first visitor sees data rather than an empty tab.
    await refresh_wod_cache(force=True)
    return await read_cached_wods(days)
