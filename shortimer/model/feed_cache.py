"""The Mongo-backed rows each `cache/*.py` module reads and writes."""

from datetime import datetime
from typing import ClassVar

from pydantic import Field
from pymongo import IndexModel

from shortimer.model.base import MongoDocument
from shortimer.model.feed_item import DatedFeedItem
from shortimer.model.gym import GymProvider
from shortimer.util.time import utcnow


class Concept2CacheEntry(MongoDocument, DatedFeedItem):
    """One cached Concept2 day. `id` is the date, isoformat — Concept2 never
    revises a published day, so the date alone is a stable, unique key."""

    id: str  # type: ignore[assignment]
    fetched_at: datetime = Field(default_factory=utcnow)

    class Settings:
        """Beanie collection name and indexes."""

        name = "concept2_cache"
        indexes: ClassVar[list[IndexModel]] = [IndexModel([("date", 1)])]


class WodCacheEntry(MongoDocument, DatedFeedItem):
    """One cached crossfit.com day. `id` is the date, isoformat."""

    id: str  # type: ignore[assignment]
    fetched_at: datetime = Field(default_factory=utcnow)

    class Settings:
        """Beanie collection name and indexes."""

        name = "wod_cache"
        indexes: ClassVar[list[IndexModel]] = [IndexModel([("date", 1)])]


class GymCacheEntry(MongoDocument, DatedFeedItem):
    """One cached day for one gym. `id` is `{fingerprint}:{date}` — see
    `cache/gym.py` for why the key has to include the gym fingerprint."""

    id: str  # type: ignore[assignment]
    gym: str
    provider: GymProvider
    fetched_at: datetime = Field(default_factory=utcnow)

    class Settings:
        """Beanie collection name and indexes."""

        name = "gym_cache"
        indexes: ClassVar[list[IndexModel]] = [IndexModel([("gym", 1), ("date", -1)])]


class HybridRotationCache(MongoDocument):
    """The single cached Hybrid Calisthenics rotation. There's only ever one,
    so it lives at the fixed id `"rotation"` rather than one row per date."""

    id: str
    days: dict[str, list[str]]
    fetched_at: datetime = Field(default_factory=utcnow)

    class Settings:
        """Beanie collection name."""

        name = "hybrid_cache"
