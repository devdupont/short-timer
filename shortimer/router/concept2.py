"""Read-only feed of recent Concept2 erg Workouts of the Day.

Reads come straight from the Mongo-backed cache (see `concept2_cache`), which a
daily background task keeps warm — no request ever waits on Concept2. The
"already saved" cross-reference is recomputed per request since it changes as
the user saves workouts.
"""

from shortimer.cache.concept2 import CACHE_DAYS, get_wods
from shortimer.router._feed import build_feed_router
from shortimer.service.concept2 import Concept2Wod


class Concept2WodEntry(Concept2Wod):
    """A Concept2 workout plus whether it's already in the user's library."""

    saved_workout_id: str | None = None


router = build_feed_router(
    prefix="/api/concept2",
    tag="concept2",
    path="/wods",
    get_wods=get_wods,
    entry_cls=Concept2WodEntry,
    cache_days=CACHE_DAYS,
)
