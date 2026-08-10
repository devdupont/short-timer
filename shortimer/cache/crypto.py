"""Encryption for per-user credentials at rest.

Users hand us credentials for third-party services — a Wodify API key today,
more later — and those are *their* secrets, not ours. Storing them as
plaintext would mean a leaked database backup hands over every user's gym
account along with it, so they're encrypted before they ever reach Mongo.

Keys come from ``SECRETS_KEYS`` (see .env.example), newest first. Fernet gives
us authenticated encryption, and layering `MultiFernet` over the list means a
key can be rotated by prepending a new one: fresh writes use the new key while
existing values still decrypt under the old. Values re-encrypt under the
primary key whenever they're rewritten.

Nothing here is required to boot. A deployment with no keys configured runs
exactly as before and simply can't store credentials — that keeps a live
server from breaking the moment this ships, and it's why `is_configured()`
exists rather than an import-time assertion.

A stored value is a `SecretBox`, not a bare string, so the API can report
whether a credential is set and show its last four characters without ever
decrypting — masking is a read of stored metadata, not a round trip through
the cipher.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import BaseModel

from shortimer.config import get_settings

logger = logging.getLogger(__name__)

#: Characters of a credential kept in the clear, so the UI can show which key
#: is stored without revealing it. Four is the convention card issuers use.
_HINT_CHARS = 4

#: How a fully-hidden credential renders. Short credentials get no hint at all
#: rather than a hint that is most of the secret.
_MASK = "••••"

#: Credentials shorter than this are masked entirely — a 6-character secret
#: with 4 revealed is not meaningfully hidden.
_MIN_LENGTH_FOR_HINT = 12


class SecretsNotConfiguredError(RuntimeError):
    """Raised when a credential write is attempted with no encryption keys set."""


class SecretBox(BaseModel):
    """An encrypted credential plus the metadata needed to describe it.

    `hint` and `set_at` are deliberately outside the ciphertext: the API needs
    them on every profile read, and decrypting just to render "••••a1b2" would
    put plaintext credentials in memory for no reason.
    """

    ciphertext: str
    #: Last few characters of the plaintext, or "" when it was too short.
    hint: str = ""

    def masked(self) -> str:
        return f"{_MASK}{self.hint}" if self.hint else _MASK


class SecretStatus(BaseModel):
    """What the API reports about a credential — never the credential itself."""

    is_set: bool = False
    masked: str | None = None


@lru_cache
def _cipher() -> MultiFernet | None:
    keys = get_settings().secrets_keys
    if not keys:
        return None
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (ValueError, TypeError):
        # A malformed key is a deployment error. Log it and behave as if no
        # keys were configured, so the app still serves everything else.
        logger.exception("SECRETS_KEYS is not a valid list of Fernet keys; ignoring it.")
        return None


def is_configured() -> bool:
    """Whether this deployment can store credentials at all."""
    return _cipher() is not None


def _hint(plaintext: str) -> str:
    return plaintext[-_HINT_CHARS:] if len(plaintext) >= _MIN_LENGTH_FOR_HINT else ""


def encrypt(plaintext: str) -> SecretBox:
    """Seal a credential for storage.

    Raises `SecretsNotConfiguredError` rather than silently storing plaintext:
    a deployment missing its keys should fail the write loudly, not quietly
    downgrade every user's credentials.
    """
    cipher = _cipher()
    if cipher is None:
        raise SecretsNotConfiguredError(
            "No encryption keys configured; set SECRETS_KEYS to store credentials."
        )
    return SecretBox(ciphertext=cipher.encrypt(plaintext.encode()).decode(), hint=_hint(plaintext))


def decrypt(box: SecretBox) -> str | None:
    """Open a stored credential, or None if it can't be read.

    Returns None rather than raising when the value doesn't decrypt — a
    credential encrypted under a key that has since been dropped from
    `SECRETS_KEYS` shouldn't take down the request that happened to read it.
    The caller treats it the same as an unset credential.
    """
    cipher = _cipher()
    if cipher is None:
        return None
    try:
        return cipher.decrypt(box.ciphertext.encode()).decode()
    except InvalidToken:
        logger.warning("Stored credential could not be decrypted; treating it as unset.")
        return None


def generate_key() -> str:
    """A fresh key suitable for SECRETS_KEYS (see .env.example)."""
    return Fernet.generate_key().decode()
