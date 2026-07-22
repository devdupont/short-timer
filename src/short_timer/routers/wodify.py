"""Read-only feed of the current user's gym workouts.

Mirrors `routers/wods.py`, except the gym is resolved from the caller's own
config rather than being the same for everyone — so this is the one feed whose
contents depend on who is asking. An unconfigured user gets an empty list and a
flag saying why, rather than a 404 the UI would have to special-case.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from short_timer.auth import current_owner, require_session
from short_timer.db import get_workouts_collection
from short_timer.dedup import source_hash
from short_timer.users import get_user
from short_timer.wodify import GymWod
from short_timer.wodify_cache import CACHE_DAYS, get_wods, resolve_source

router = APIRouter(prefix="/api/gym", tags=["gym"], dependencies=[Depends(require_session)])

_DEFAULT_DAYS = 7


class GymWodEntry(GymWod):
    """A gym workout plus whether it's already in this user's library."""

    saved_workout_id: str | None = None


class GymFeed(BaseModel):
    """The feed, plus enough context for the UI to explain an empty one."""

    configured: bool
    wods: list[GymWodEntry] = []


@router.get("/wods", response_model=GymFeed)
async def list_gym_wods(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=CACHE_DAYS),
    owner_id: str = Depends(current_owner),
) -> GymFeed:
    user = await get_user(owner_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    source = resolve_source(user)
    if source is None:
        # Not an error: the user simply hasn't connected a gym, or has one
        # saved but switched it off.
        return GymFeed(configured=False)

    wods = await get_wods(source, days)
    collection = get_workouts_collection()
    entries: list[GymWodEntry] = []
    for wod in wods:
        saved = await collection.find_one(
            {"owner_id": owner_id, "source_hash": source_hash(wod.text)}, {"_id": 1}
        )
        entries.append(
            GymWodEntry(**wod.model_dump(), saved_workout_id=saved["_id"] if saved else None)
        )
    return GymFeed(configured=True, wods=entries)
