"""Read-only feed of recent crossfit.com Workouts of the Day.

Reads come straight from the Mongo-backed cache (see `wod_cache`), which a
daily background task keeps warm — no request ever waits on crossfit.com. The
"already saved" cross-reference is recomputed per request since it changes as
the user saves workouts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from short_timer.auth import current_owner, require_session
from short_timer.crossfit import Wod
from short_timer.db import get_workouts_collection
from short_timer.dedup import source_hash
from short_timer.wod_cache import CACHE_DAYS, get_wods

router = APIRouter(prefix="/api/wods", tags=["wods"], dependencies=[Depends(require_session)])

_DEFAULT_DAYS = 7


class WodEntry(Wod):
    """A crossfit.com WOD plus whether it's already in the user's library."""

    saved_workout_id: str | None = None


@router.get("", response_model=list[WodEntry])
async def list_wods(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=CACHE_DAYS),
    owner_id: str = Depends(current_owner),
) -> list[WodEntry]:
    wods = await get_wods(days)
    collection = get_workouts_collection()
    entries: list[WodEntry] = []
    for wod in wods:
        saved = await collection.find_one(
            {"owner_id": owner_id, "source_hash": source_hash(wod.text)}, {"_id": 1}
        )
        entries.append(
            WodEntry(**wod.model_dump(), saved_workout_id=saved["_id"] if saved else None)
        )
    return entries
