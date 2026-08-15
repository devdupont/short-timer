"""Read-only feed of the current user's gym workouts, and the source registry.

Mirrors `routers/wods.py`, except the gym is resolved from the caller's own
config rather than being the same for everyone — so this is the one feed whose
contents depend on who is asking. An unconfigured user gets an empty list and a
flag saying why, rather than a 404 the UI would have to special-case.

`/providers` and `/health` exist so the settings screen is data-driven: the
first says what can be connected and what to call its fields, the second says
whether a connection the user already made is actually working. Adding a gym
platform is then a server change only.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from shortimer.auth.session import current_owner, require_session
from shortimer.cache.db import get_workouts_collection
from shortimer.cache.gym import (
    CACHE_DAYS,
    ConnectionHealth,
    connection_health,
    get_wods,
    resolve_source,
)
from shortimer.errors import not_found
from shortimer.model.gym import GymWod
from shortimer.service.gym_providers import GymProviderInfo, all_info, spec_for
from shortimer.users import get_user
from shortimer.util.dedup import source_hash

router = APIRouter(prefix="/api/gym", tags=["gym"], dependencies=[Depends(require_session)])

_DEFAULT_DAYS = 7


class GymWodEntry(GymWod):
    """A gym workout plus whether it's already in this user's library."""

    saved_workout_id: str | None = None
    #: Stamped from the provider registry rather than left to the client, so
    #: the card can say "View on SugarWOD" without the frontend keeping its own
    #: copy of a platform list that would drift the moment one is added.
    link_label: str = ""


class GymFeed(BaseModel):
    """The feed, plus enough context for the UI to explain an empty one."""

    configured: bool
    wods: list[GymWodEntry] = []


@router.get("/providers", response_model=list[GymProviderInfo])
async def list_providers() -> list[GymProviderInfo]:
    """Every gym platform this server can connect to, in offer order.

    Static, but behind the session gate like everything else here — it names
    integrations and their setup instructions, which is product surface rather
    than public documentation.
    """
    return all_info()


@router.get("/health", response_model=list[ConnectionHealth])
async def list_connection_health(owner_id: str = Depends(current_owner)) -> list[ConnectionHealth]:
    """Whether each of the caller's stored connections has ever fetched."""
    user = await get_user(owner_id)
    if user is None:
        not_found("User not found")
    return await connection_health(user)


@router.get("/wods", response_model=GymFeed)
async def list_gym_wods(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=CACHE_DAYS),
    owner_id: str = Depends(current_owner),
) -> GymFeed:
    """The caller's own gym feed — empty and `configured=False` if they have none set up."""
    user = await get_user(owner_id)
    if user is None:
        not_found("User not found")

    source = resolve_source(user)
    if source is None:
        # Not an error: the user simply hasn't connected a gym, or has one
        # saved but switched off.
        return GymFeed(configured=False)

    wods = await get_wods(source, days)
    collection = get_workouts_collection()
    entries: list[GymWodEntry] = []
    for wod in wods:
        saved = await collection.find_one(
            {"owner_id": owner_id, "source_hash": source_hash(wod.text)}, {"_id": 1}
        )
        entries.append(
            GymWodEntry(
                **wod.model_dump(),
                saved_workout_id=saved["_id"] if saved else None,
                link_label=spec_for(wod.provider).info.link_label,
            )
        )
    return GymFeed(configured=True, wods=entries)
