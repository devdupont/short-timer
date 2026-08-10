"""Bearer tokens we hand out: sessions, invites, verification, reset, API keys.

All five are the same shape — a long random string the holder presents, stored
only as a hash so the database can check it without being able to produce it.
That property is what makes a leaked backup useless: an attacker gets hashes
of tokens that have already been delivered, not tokens.

The hash is plain SHA-256 rather than a password KDF, deliberately. A password
is short, human-chosen and drawn from a small distribution, which is what makes
a slow hash worth its cost. These are 256 bits of uniform randomness; there is
no dictionary to attack, so iterating a hash would buy nothing and slow every
authenticated request down.
"""

from __future__ import annotations

import hashlib
import secrets

#: 32 bytes → 256 bits, well past OWASP's 64-bit floor for a session id, and
#: free.
_TOKEN_BYTES = 32


def new_token() -> str:
    """A fresh token. Returned once and never recoverable from storage."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The stored form of a token."""
    return hashlib.sha256(token.encode()).hexdigest()


def token_prefix(token: str, length: int = 8) -> str:
    """A short, non-secret handle for a token, so a UI can name one.

    An API tokens list has to let someone tell two tokens apart in order to
    revoke the right one, and it can't show the token. The first few characters
    leak a negligible fraction of the entropy and solve that.
    """
    return token[:length]
