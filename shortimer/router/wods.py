"""Read-only feed of recent crossfit.com Workouts of the Day.

Reads come straight from the Mongo-backed cache (see `wod_cache`), which a
daily background task keeps warm — no request ever waits on crossfit.com. The
"already saved" cross-reference is recomputed per request since it changes as
the user saves workouts.
"""

from shortimer.cache.crossfit import CACHE_DAYS, get_wods
from shortimer.router._feed import build_feed_router
from shortimer.service.crossfit import Wod


class WodEntry(Wod):
    """A crossfit.com WOD plus whether it's already in the user's library."""

    saved_workout_id: str | None = None


router = build_feed_router(
    prefix="/api/wods",
    tag="wods",
    path="",
    get_wods=get_wods,
    entry_cls=WodEntry,
    cache_days=CACHE_DAYS,
)
