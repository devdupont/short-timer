import pytest

from short_timer.auth import check_passcode, create_session_token, verify_session_token
from short_timer.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_check_passcode() -> None:
    assert check_passcode("test-passcode") is True
    assert check_passcode("wrong") is False


def test_session_token_round_trip() -> None:
    token = create_session_token()
    assert verify_session_token(token) is True


def test_tampered_token_is_rejected() -> None:
    token = create_session_token()
    assert verify_session_token(token + "x") is False


def test_garbage_token_is_rejected() -> None:
    assert verify_session_token("not-a-real-token") is False
