"""Mongo-backed cache of crossfit.com Workout of the Day pages.

crossfit.com publishes one workout per day, so there's no reason to fetch it
on demand. A background task refreshes the cache once a day and every request
reads from Mongo. That keeps our traffic to crossfit.com to a handful of
requests per day, survives restarts, and is shared across instances — none of
which was true of the previous in-process cache.

It also means the WOD tab keeps working when crossfit.com is unreachable: we
just serve the last good copy.
"""

import logging
from datetime import datetime, timedelta

from shortimer.cache._feed import FeedCache
from shortimer.cache.parse import SOURCE_CROSSFIT
from shortimer.model.feed_cache import WodCacheEntry
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


class _Cache(FeedCache[WodCacheEntry]):
    """`FeedCache` bound to `WodCacheEntry`."""

    document_model = WodCacheEntry


def _to_wod(entry: WodCacheEntry) -> Wod:
    """A cached row as the `Wod` shape callers work with."""
    return Wod(date=entry.date, title=entry.title, text=entry.text, url=entry.url)


async def last_refreshed_at() -> datetime | None:
    """When the cache was most recently written, if ever."""
    return await _Cache.last_refreshed_at()


async def read_cached_wods(days: int) -> list[Wod]:
    """Most recent `days` cached WODs, newest first."""
    cursor = WodCacheEntry.find().sort("-date").limit(days)
    return [_to_wod(entry) async for entry in cursor]


async def refresh_wod_cache(*, force: bool = False) -> int:
    """Fetch from crossfit.com and upsert into the cache. Returns rows written.

    Days that fail upstream are skipped rather than evicting a good cached
    copy, so a bad fetch degrades to stale data instead of an empty tab.
    """
    if not force and await _Cache.is_fresh(MIN_REFRESH_INTERVAL):
        logger.debug("WOD cache still fresh; skipping refresh.")
        return 0

    wods = await fetch_recent_wods(CACHE_DAYS)
    if not wods:
        logger.warning("crossfit.com returned no workouts; keeping existing cache.")
        return 0

    for wod in wods:
        await WodCacheEntry(
            id=wod.date.isoformat(), date=wod.date, title=wod.title, text=wod.text, url=wod.url
        ).save()
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
    cursor = WodCacheEntry.find().sort("-date").limit(limit)
    # Rest days have nothing to time, and the UI won't offer to load them.
    candidates = [
        (entry.text, entry.title or None) async for entry in cursor if not is_rest_day(entry.text)
    ]
    parsed = await _Cache.warm_parse_pool(
        candidates, parse=parse_workout_text, source=SOURCE_CROSSFIT, purpose="prewarm:crossfit"
    )
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
