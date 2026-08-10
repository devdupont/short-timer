"""Single-use tokens delivered by email: address verification and password reset.

Both are the same mechanism with different stakes. A verification token proves
someone can read an inbox. A reset token *takes over an account*, which is why
it expires in an hour rather than two days, why issuing one invalidates any
outstanding ones, and why redeeming one ends every session the account has.

Redemption deletes the row rather than flagging it used. There's nothing to
learn from a spent token, and a row that can't be presented twice is easier to
reason about than one whose validity depends on remembering to check a flag.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from shortimer.auth.tokens import hash_token, new_token
from shortimer.cache.db import get_email_tokens_collection
from shortimer.config import get_settings

logger = logging.getLogger(__name__)


class TokenKind(StrEnum):
    VERIFY = "verify"
    RESET = "reset"


def _ttl(kind: TokenKind) -> timedelta:
    settings = get_settings()
    if kind is TokenKind.RESET:
        return timedelta(minutes=settings.reset_ttl_minutes)
    return timedelta(hours=settings.verify_ttl_hours)


async def issue(kind: TokenKind, *, user_id: str, email: str) -> str:
    """Mint a token and return it raw. Only the hash is stored.

    Any outstanding token of the same kind for the same user is dropped first.
    Otherwise every "I didn't get the email" retry would leave another live
    credential lying in an inbox, and the oldest of them would still work.
    """
    await get_email_tokens_collection().delete_many({"user_id": user_id, "kind": kind.value})

    token = new_token()
    now = datetime.now(UTC)
    await get_email_tokens_collection().insert_one(
        {
            "_id": hash_token(token),
            "kind": kind.value,
            "user_id": user_id,
            "email": email,
            "created_at": now,
            "expires_at": now + _ttl(kind),
        }
    )
    return token


async def redeem(kind: TokenKind, token: str) -> str | None:
    """Spend a token, returning the user it belongs to, or None.

    Expiry is checked here rather than left to the TTL index, for the same
    reason as sessions: Mongo's sweep lags by up to a minute and mongomock
    never runs one, so an index-only check would let a stale reset token
    through.
    """
    key = hash_token(token)
    doc = await get_email_tokens_collection().find_one_and_delete({"_id": key, "kind": kind.value})
    if doc is None:
        return None

    expires_at = doc.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) >= expires_at:
            return None
    else:
        return None

    user_id = doc.get("user_id")
    return user_id if isinstance(user_id, str) and user_id else None


async def revoke_all(user_id: str, kind: TokenKind) -> int:
    """Drop a user's outstanding tokens of one kind.

    Used when the thing a token would have granted has happened another way —
    verifying by redeeming an emailed invite, say.
    """
    result = await get_email_tokens_collection().delete_many(
        {"user_id": user_id, "kind": kind.value}
    )
    return int(result.deleted_count)
