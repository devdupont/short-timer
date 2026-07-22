"""Read-only feed of the Hybrid Calisthenics rotation, projected onto dates.

Reads come from the Mongo-backed cache (see `hybrid_cache`), which a background
task keeps warm — no request ever waits on hybridcalisthenics.com.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from short_timer.auth import current_owner, require_session
from short_timer.db import get_workouts_collection
from short_timer.dedup import source_hash
from short_timer.hybrid import HybridWorkout
from short_timer.hybrid_cache import CACHE_DAYS, get_wods

router = APIRouter(prefix="/api/hybrid", tags=["hybrid"], dependencies=[Depends(require_session)])

_DEFAULT_DAYS = 7


class HybridWodEntry(HybridWorkout):
    """A rotation day plus whether it's already in the user's library."""

    saved_workout_id: str | None = None


@router.get("/wods", response_model=list[HybridWodEntry])
async def list_hybrid_wods(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=CACHE_DAYS),
    owner_id: str = Depends(current_owner),
) -> list[HybridWodEntry]:
    wods = await get_wods(days)
    collection = get_workouts_collection()
    entries: list[HybridWodEntry] = []
    for wod in wods:
        saved = await collection.find_one(
            {"owner_id": owner_id, "source_hash": source_hash(wod.text)}, {"_id": 1}
        )
        entries.append(
            HybridWodEntry(**wod.model_dump(), saved_workout_id=saved["_id"] if saved else None)
        )
    return entries
