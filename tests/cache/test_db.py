"""The Mongo client's own configuration, as opposed to what's stored through it."""

from shortimer.config import get_settings


def test_database_client_fails_fast_rather_than_hanging() -> None:
    """A slow failure is worse than a fast one when every request pays for it."""
    assert get_settings().mongodb_timeout_ms <= 10_000
