"""Mongo-backed cache of the Hybrid Calisthenics weekly rotation.

Unlike the crossfit.com and Concept2 caches, this stores *one* document rather
than one per day. The source publishes a rotation, not a dated post, so there
is nothing per-date to keep — "what was Tuesday's workout" is answered by the
rotation itself, for any Tuesday, past or future.

That makes the refresh cheap and the history free: `get_wods` projects the
cached rotation onto the last N dates on the way out, so the feed looks exactly
like the dated ones on the home page without ever having stored a dated row.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from shortimer.cache._feed import FeedCache
from shortimer.cache.parse import SOURCE_HYBRID
from shortimer.model.feed_cache import HybridRotationCache
from shortimer.service.hybrid import HybridWorkout, Rotation, fetch_rotation, is_rest_day
from shortimer.service.llm import parse_workout_text

logger = logging.getLogger(__name__)

#: How many days of the rotation to project for the UI.
CACHE_DAYS = 14
#: The rotation changes rarely, so this is much longer than the daily feeds'.
MIN_REFRESH_INTERVAL = timedelta(days=7)
#: How long the background task sleeps between refreshes.
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

#: There's only ever one rotation, so it lives at a fixed key.
_DOCUMENT_ID = "rotation"


class _Cache(FeedCache[HybridRotationCache]):
    """`FeedCache` bound to `HybridRotationCache`."""

    document_model = HybridRotationCache


async def last_refreshed_at() -> datetime | None:
    """When the rotation was most recently written, if ever."""
    return await _Cache.last_refreshed_at({"_id": _DOCUMENT_ID})


async def read_cached_rotation() -> Rotation | None:
    """The stored rotation, or None if it's never been fetched or is empty."""
    entry = await HybridRotationCache.get(_DOCUMENT_ID)
    if entry is None or not entry.days:
        return None
    return Rotation(days=entry.days)


async def refresh_hybrid_cache(*, force: bool = False) -> bool:
    """Re-fetch the rotation. True when it was written.

    A failed fetch leaves the cached rotation in place — a routine that hasn't
    changed in years shouldn't vanish from the home page because the site was
    briefly down.
    """
    if not force and await _Cache.is_fresh(MIN_REFRESH_INTERVAL, {"_id": _DOCUMENT_ID}):
        logger.debug("Hybrid rotation still fresh; skipping refresh.")
        return False

    rotation = await fetch_rotation()
    if rotation is None:
        logger.warning("Could not fetch the Hybrid rotation; keeping existing cache.")
        return False

    await HybridRotationCache(
        id=_DOCUMENT_ID, days=rotation.days, fetched_at=datetime.now(UTC)
    ).save()
    logger.info("Refreshed the Hybrid rotation (%d day(s)).", len(rotation.days))
    return True


async def ensure_wods_parsed() -> int:
    """Warm the shared parse pool with each distinct day of the rotation.

    A six-day rotation over three workouts means three distinct texts, so this
    costs three model calls once and nothing thereafter — the pool is keyed on
    text, and every Monday and Thursday is the same workout.
    """
    rotation = await read_cached_rotation()
    if rotation is None:
        return 0

    # Rest days have nothing to load, and repeats within the rotation are the
    # same text — both are skipped before warming the pool. `dict.fromkeys`
    # dedupes while keeping first-seen order.
    texts = ("\n".join(lines) for lines in rotation.days.values())
    eligible = [text for text in texts if text and not is_rest_day(text)]
    candidates: list[tuple[str, str | None]] = [(text, None) for text in dict.fromkeys(eligible)]

    parsed = await _Cache.warm_parse_pool(
        candidates, parse=parse_workout_text, source=SOURCE_HYBRID, purpose="prewarm:hybrid"
    )
    if parsed:
        logger.info("Pre-parsed %d Hybrid workout(s).", parsed)
    return parsed


async def get_wods(days: int, *, today: date | None = None) -> list[HybridWorkout]:
    """The rotation projected onto the last `days` dates, newest first."""
    rotation = await read_cached_rotation()
    if rotation is None:
        # Cold cache (first boot, or fresh database) — populate it inline so
        # the first visitor sees data rather than an empty feed.
        await refresh_hybrid_cache(force=True)
        rotation = await read_cached_rotation()
    if rotation is None:
        return []

    anchor = today or datetime.now().date()
    projected = (
        rotation.for_date(anchor - timedelta(days=offset)) for offset in range(max(1, days))
    )
    return [wod for wod in projected if wod is not None]
