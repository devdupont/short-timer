"""Fixtures shared by more than one router test module."""

from collections.abc import Generator

import pytest

from shortimer.cache import crypto
from shortimer.cache.crypto import generate_key
from shortimer.config import get_settings


@pytest.fixture
def secrets_configured(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """An encryption key, for the endpoints that store or read gym credentials.

    Not autouse: only `test_me.py` and `test_gym.py` touch credentials, so
    modules that don't need it opt in with `pytestmark`.
    """
    monkeypatch.setenv("SECRETS_KEYS", generate_key())
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
