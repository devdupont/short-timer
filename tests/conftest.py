import os

os.environ.setdefault("APP_PASSCODE", "test-passcode")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME", "short_timer_test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest
from mongomock_motor import AsyncMongoMockClient

from short_timer import db as db_module
from short_timer import llm as llm_module


@pytest.fixture(autouse=True)
def _mock_mongo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets an isolated in-memory Mongo instead of a real one."""
    client = AsyncMongoMockClient()
    monkeypatch.setattr(db_module, "get_client", lambda: client)


@pytest.fixture(autouse=True)
def _fresh_anthropic_client() -> None:
    """Drop the cached Anthropic client between tests.

    The app caches it so the connection pool is reused (and not leaked) across
    requests, but each test gets its own event loop and may patch the client
    class, so a client held over from a previous test would be wrong.
    """
    llm_module._client.cache_clear()
