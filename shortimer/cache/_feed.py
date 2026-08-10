"""Shared behaviour behind every Mongo-backed feed cache.

`cache/concept2.py`, `crossfit.py`, `gym.py` and `hybrid.py` each answer two
questions the same way regardless of source: how fresh the cache is, and how
its rows get pre-parsed into the shared parse pool (see `cache/parse.py`).
Fetching and storing stay with each cache module rather than moving here,
since that's exactly where they differ for real: crossfit.com re-fetches its
whole window every time, Concept2 fetches only the days it's missing, a gym
is scoped to one fingerprint, and Hybrid Calisthenics replaces a single
rotation document instead of upserting many dated rows.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from beanie import Document

from shortimer.cache.parse import find_parse, mark_permanent, remember_parse
from shortimer.model.workout import Workout
from shortimer.util.time import as_utc

logger = logging.getLogger(__name__)


class FeedCache[DocT: Document]:
    """Freshness and parse-pool warming for one Document-backed feed cache.

    Subclasses set `document_model` to the Beanie `Document` their rows are
    stored as; everything here is a classmethod, since a feed cache has no
    per-instance state — the freshness gate and the pool are properties of
    the collection, not of any object that lives longer than a call.
    """

    document_model: ClassVar[type[Document]]

    @classmethod
    async def last_refreshed_at(cls, filter_query: dict[str, Any] | None = None) -> datetime | None:
        """When a row matching `filter_query` was most recently written, if ever."""
        doc = await cls.document_model.find(filter_query or {}).sort("-fetched_at").first_or_none()
        fetched_at = getattr(doc, "fetched_at", None)
        if not isinstance(fetched_at, datetime):
            return None
        return as_utc(fetched_at)

    @classmethod
    async def is_fresh(
        cls, min_interval: timedelta, filter_query: dict[str, Any] | None = None
    ) -> bool:
        """Whether the cache was refreshed more recently than `min_interval` ago."""
        last = await cls.last_refreshed_at(filter_query)
        return last is not None and datetime.now(UTC) - last < min_interval

    @staticmethod
    async def warm_parse_pool(
        candidates: list[tuple[str, str | None]],
        *,
        parse: Callable[..., Awaitable[Workout]],
        source: str,
        purpose: str,
        promote_existing: bool = True,
    ) -> int:
        """Pre-parse eligible `(text, title)` pairs. Returns how many were newly parsed.

        `parse` is passed in — usually `service.llm.parse_workout_text` —
        rather than imported here, so each cache module keeps its own
        top-level reference to patch in tests, the same as before this was
        shared.

        A candidate already in the pool costs nothing — `promote_existing`
        controls whether finding one there also promotes it to `source`'s
        retention, which is right for a source everyone shares (crossfit.com,
        Concept2, the Hybrid rotation) and wrong for a gym's own programming,
        which stays on the ordinary user-retention clock even when a member
        happened to paste it first (see `cache/parse.py`'s `PERMANENT_SOURCES`).
        One bad item logs and is skipped rather than stopping the rest.
        """
        parsed = 0
        for text, title in candidates:
            if not text:
                continue
            if await find_parse(text) is not None:
                if promote_existing:
                    await mark_permanent(text, source=source)
                continue
            try:
                workout = await parse(text, name_hint=title, purpose=purpose)
            except Exception:  # one bad item shouldn't stop the rest
                logger.exception("Could not pre-parse %s item %r", source, text[:40])
                continue
            await remember_parse(workout, source=source)
            parsed += 1
        return parsed
