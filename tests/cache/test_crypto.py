"""Encrypting/decrypting stored credentials, masking, and key rotation."""

from collections.abc import Generator

import pytest

from shortimer.cache import crypto
from shortimer.cache.crypto import (
    SecretBox,
    SecretsNotConfiguredError,
    decrypt,
    encrypt,
    generate_key,
    is_configured,
)
from shortimer.config import get_settings


@pytest.fixture
def _keys(monkeypatch: pytest.MonkeyPatch) -> Generator[str]:
    """Configure a single encryption key for the duration of a test."""
    key = generate_key()
    monkeypatch.setenv("SECRETS_KEYS", key)
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    yield key
    get_settings.cache_clear()
    crypto._cipher.cache_clear()


@pytest.fixture
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Configure no encryption keys at all, for the "credential storage unavailable" tests."""
    monkeypatch.setenv("SECRETS_KEYS", "")
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._cipher.cache_clear()


def test_round_trip(_keys: str) -> None:
    """Encrypting then decrypting a credential returns the original plaintext."""
    box = encrypt("wodify-api-key-abcd1234")
    assert decrypt(box) == "wodify-api-key-abcd1234"


def test_ciphertext_does_not_contain_plaintext(_keys: str) -> None:
    """The stored ciphertext never contains the plaintext credential as a substring."""
    secret = "super-secret-credential"
    box = encrypt(secret)
    assert secret not in box.ciphertext


def test_hint_reveals_only_last_four(_keys: str) -> None:
    """A long-enough credential's hint is its last four characters, masked the rest of the way."""
    box = encrypt("wodify-api-key-abcd1234")
    assert box.hint == "1234"
    assert box.masked() == "••••1234"


def test_short_secret_is_fully_masked(_keys: str) -> None:
    """A hint on a short secret would give away most of it."""
    box = encrypt("short")
    assert box.hint == ""
    assert box.masked() == "••••"


def test_encryption_is_not_deterministic(_keys: str) -> None:
    """Two encryptions of the same value must not be linkable."""
    assert encrypt("same-value-here").ciphertext != encrypt("same-value-here").ciphertext


def test_rotation_reads_values_written_under_an_older_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepending a new primary key still decrypts values written under the old one, and
    fresh writes use the new key, which the old key alone cannot read."""
    old = generate_key()
    monkeypatch.setenv("SECRETS_KEYS", old)
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    box = encrypt("credential-written-earlier")

    # Rotate: a new primary key, with the old one retained for reads.
    new = generate_key()
    monkeypatch.setenv("SECRETS_KEYS", f"{new},{old}")
    get_settings.cache_clear()
    crypto._cipher.cache_clear()

    assert decrypt(box) == "credential-written-earlier"
    # And fresh writes use the new key, which the old key alone cannot read.
    rewritten = encrypt("credential-written-later")
    monkeypatch.setenv("SECRETS_KEYS", old)
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    assert decrypt(rewritten) is None

    get_settings.cache_clear()
    crypto._cipher.cache_clear()


def test_undecryptable_value_reads_as_unset(_keys: str) -> None:
    """A dropped key shouldn't take down the request that reads it."""
    assert decrypt(SecretBox(ciphertext="not-a-real-token", hint="1234")) is None


def test_encrypt_without_keys_raises(_no_keys: None) -> None:
    """With no keys configured, `is_configured` is False and `encrypt` refuses to store plaintext."""
    assert is_configured() is False
    with pytest.raises(SecretsNotConfiguredError):
        encrypt("anything")


def test_decrypt_without_keys_returns_none(_no_keys: None) -> None:
    """With no keys configured, `decrypt` degrades to None rather than raising."""
    assert decrypt(SecretBox(ciphertext="whatever")) is None


def test_malformed_key_disables_storage_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SECRETS_KEYS value that isn't a valid Fernet key behaves like no keys, not a crash."""
    monkeypatch.setenv("SECRETS_KEYS", "not-a-valid-fernet-key")
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    try:
        assert is_configured() is False
    finally:
        get_settings.cache_clear()
        crypto._cipher.cache_clear()
