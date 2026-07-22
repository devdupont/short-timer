"""Read-only feed of recent Concept2 erg Workouts of the Day.

Reads come straight from the Mongo-backed cache (see `concept2_cache`), which a
daily background task keeps warm — no request ever waits on Concept2. The
"already saved" cross-reference is recomputed per request since it changes as
the user saves workouts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from short_timer.auth import current_owner, require_session
from short_timer.concept2 import Concept2Wod
from short_timer.concept2_cache import CACHE_DAYS, get_wods
from short_timer.db import get_workouts_collection
from short_timer.dedup import source_hash

router = APIRouter(
    prefix="/api/concept2", tags=["concept2"], dependencies=[Depends(require_session)]
)

_DEFAULT_DAYS = 7


class Concept2WodEntry(Concept2Wod):
    """A Concept2 workout plus whether it's already in the user's library."""

    saved_workout_id: str | None = None


@router.get("/wods", response_model=list[Concept2WodEntry])
async def list_concept2_wods(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=CACHE_DAYS),
    owner_id: str = Depends(current_owner),
) -> list[Concept2WodEntry]:
    wods = await get_wods(days)
    collection = get_workouts_collection()
    entries: list[Concept2WodEntry] = []
    for wod in wods:
        saved = await collection.find_one(
            {"owner_id": owner_id, "source_hash": source_hash(wod.text)}, {"_id": 1}
        )
        entries.append(
            Concept2WodEntry(**wod.model_dump(), saved_workout_id=saved["_id"] if saved else None)
        )
    return entries
