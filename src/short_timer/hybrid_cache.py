"""Mongo-backed cache of the Hybrid Calisthenics weekly rotation.

Unlike the crossfit.com and Concept2 caches, this stores *one* document rather
than one per day. The source publishes a rotation, not a dated post, so there
is nothing per-date to keep — "what was Tuesday's workout" is answered by the
rotation itself, for any Tuesday, past or future.

That makes the refresh cheap and the history free: `get_wods` projects the
cached rotation onto the last N dates on the way out, so the feed looks exactly
like the dated ones on the home page without ever having stored a dated row.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from short_timer.db import get_hybrid_cache_collection
from short_timer.hybrid import HybridWorkout, Rotation, fetch_rotation, is_rest_day
from short_timer.llm import parse_workout_text
from short_timer.parse_cache import (
    SOURCE_HYBRID,
    find_parse,
    mark_permanent,
    remember_parse,
)

logger = logging.getLogger(__name__)

#: How many days of the rotation to project for the UI.
CACHE_DAYS = 14
#: The rotation changes rarely, so this is much longer than the daily feeds'.
MIN_REFRESH_INTERVAL = timedelta(days=7)
#: How long the background task sleeps between refreshes.
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

#: There's only ever one rotation, so it lives at a fixed key.
_DOCUMENT_ID = "rotation"


async def last_refreshed_at() -> datetime | None:
    """When the rotation was most recently written, if ever."""
    doc = await get_hybrid_cache_collection().find_one({"_id": _DOCUMENT_ID})
    if doc is None:
        return None
    fetched = doc.get("fetched_at")
    if not isinstance(fetched, datetime):
        return None
    # Mongo returns naive UTC datetimes; make comparisons safe.
    return fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)


async def read_cached_rotation() -> Rotation | None:
    doc = await get_hybrid_cache_collection().find_one({"_id": _DOCUMENT_ID})
    if doc is None:
        return None
    days = doc.get("days")
    if not isinstance(days, dict) or not days:
        return None
    return Rotation(days={str(k): [str(x) for x in v] for k, v in days.items()})


async def refresh_hybrid_cache(*, force: bool = False) -> bool:
    """Re-fetch the rotation. True when it was written.

    A failed fetch leaves the cached rotation in place — a routine that hasn't
    changed in years shouldn't vanish from the home page because the site was
    briefly down.
    """
    if not force:
        last = await last_refreshed_at()
        if last is not None and datetime.now(UTC) - last < MIN_REFRESH_INTERVAL:
            logger.debug("Hybrid rotation still fresh; skipping refresh.")
            return False

    rotation = await fetch_rotation()
    if rotation is None:
        logger.warning("Could not fetch the Hybrid rotation; keeping existing cache.")
        return False

    await get_hybrid_cache_collection().replace_one(
        {"_id": _DOCUMENT_ID},
        {"_id": _DOCUMENT_ID, "days": rotation.days, "fetched_at": datetime.now(UTC)},
        upsert=True,
    )
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

    parsed = 0
    seen: set[str] = set()
    for lines in rotation.days.values():
        text = "\n".join(lines)
        # Rest days have nothing to load, and repeats within the rotation are
        # the same text — both are skipped before we reach the model.
        if not text or text in seen or is_rest_day(text):
            continue
        seen.add(text)
        if await find_parse(text) is not None:
            await mark_permanent(text, source=SOURCE_HYBRID)
            continue
        try:
            workout = await parse_workout_text(text, purpose="prewarm:hybrid")
        except Exception:  # one bad day shouldn't stop the rest
            logger.exception("Could not pre-parse Hybrid workout %r", text[:40])
            continue
        await remember_parse(workout, source=SOURCE_HYBRID)
        parsed += 1

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
