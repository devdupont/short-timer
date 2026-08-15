"""Factory behind every read-only "recent days" feed endpoint.

`router/wods.py`, `concept2.py` and `hybrid.py` are otherwise identical: fetch
the cached days, mark which are already in the caller's library, hand back an
`XEntry` that adds `saved_workout_id`. Only the cache module, the wire model,
and the route itself differ per feed.
"""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Query

from shortimer.auth.session import current_owner, require_session
from shortimer.cache.db import get_workouts_collection
from shortimer.model.feed_item import DatedFeedItem
from shortimer.util.dedup import source_hash

_DEFAULT_DAYS = 7


def build_feed_router[ItemT: DatedFeedItem, EntryT: DatedFeedItem](
    *,
    prefix: str,
    tag: str,
    path: str,
    get_wods: Callable[[int], Awaitable[list[ItemT]]],
    entry_cls: type[EntryT],
    cache_days: int,
) -> APIRouter:
    """Build a read-only "recent days" feed router for one source.

    `ItemT` is what `get_wods` returns; `EntryT` is `ItemT` plus
    `saved_workout_id`, which is what the route actually serves.
    """
    router = APIRouter(prefix=prefix, tags=[tag], dependencies=[Depends(require_session)])

    @router.get(path, response_model=list[entry_cls])  # type: ignore[valid-type]
    async def list_wods(
        days: int = Query(_DEFAULT_DAYS, ge=1, le=cache_days),
        owner_id: str = Depends(current_owner),
    ) -> list[EntryT]:
        """The last `days` cached workouts, each marked with the caller's saved copy, if any."""
        wods = await get_wods(days)
        collection = get_workouts_collection()
        entries: list[EntryT] = []
        for wod in wods:
            saved = await collection.find_one(
                {"owner_id": owner_id, "source_hash": source_hash(wod.text)}, {"_id": 1}
            )
            entries.append(
                entry_cls(**wod.model_dump(), saved_workout_id=saved["_id"] if saved else None)
            )
        return entries

    return router
