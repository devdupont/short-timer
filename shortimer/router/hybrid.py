"""Read-only feed of the Hybrid Calisthenics rotation, projected onto dates.

Reads come from the Mongo-backed cache (see `hybrid_cache`), which a background
task keeps warm — no request ever waits on hybridcalisthenics.com.
"""

from shortimer.cache.hybrid import CACHE_DAYS, get_wods
from shortimer.router._feed import build_feed_router
from shortimer.service.hybrid import HybridWorkout


class HybridWodEntry(HybridWorkout):
    """A rotation day plus whether it's already in the user's library."""

    saved_workout_id: str | None = None


router = build_feed_router(
    prefix="/api/hybrid",
    tag="hybrid",
    path="/wods",
    get_wods=get_wods,
    entry_cls=HybridWodEntry,
    cache_days=CACHE_DAYS,
)
