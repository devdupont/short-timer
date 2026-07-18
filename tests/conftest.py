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


@pytest.fixture(autouse=True)
def _mock_mongo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets an isolated in-memory Mongo instead of a real one."""
    client = AsyncMongoMockClient()
    monkeypatch.setattr(db_module, "get_client", lambda: client)
