"""Helpers for datetimes that came back from Mongo.

PyMongo hands back naive UTC datetimes rather than aware ones, so any value
read from a document needs normalizing before it's safe to compare against
`datetime.now(UTC)` — comparing an aware and a naive datetime raises.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """The current instant, timezone-aware in UTC."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """The same instant, guaranteed timezone-aware in UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_expired(expires_at: datetime) -> bool:
    """Whether `expires_at` has already passed, tolerating a naive value."""
    return datetime.now(UTC) >= as_utc(expires_at)
