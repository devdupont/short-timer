"""Server-side sessions.

Sessions used to be a signed cookie (`itsdangerous`), which meant they could
not be taken back: a signature stays valid until it expires no matter what
happens to the account behind it. "Log out everywhere" and "changing your
password ends every other session" are both just *deleting rows*, and there
were no rows. Now there are.

What the browser holds is an opaque random token. What's stored is its
SHA-256, never the token itself — so a leaked database dump doesn't hand
over a single live session. That's the same reasoning as storing password
hashes, applied to the credential the cookie actually carries.

Two clocks bound a session. The **idle** deadline slides forward as it's
used, and the **absolute** deadline never moves, so a session that stays
active forever still ends eventually. Both are generous here: this is a
workout timer opened sporadically on a phone at a gym, and being signed out
mid-workout is a real cost. Revocation plus re-authentication on the
sensitive actions is what carries that risk, rather than a short expiry that
would mostly just annoy the one person using it.

Expiry is enforced *here*, in application code, not by the TTL index. A TTL
index is a janitor: Mongo sweeps it about once a minute, so an expired row is
readable for a while after it should be gone. The index exists to stop the
collection growing without bound; it is not the check.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from short_timer.config import get_settings
from short_timer.db import get_sessions_collection

logger = logging.getLogger(__name__)

#: Bytes of entropy in a session token. OWASP's floor is 64 bits; 256 costs
#: nothing here and removes the question.
_TOKEN_BYTES = 32

#: How stale the idle deadline may get before a read bothers to push it
#: forward. Sliding on literally every request would mean a database write
#: per request to move a 30-day deadline by milliseconds.
_TOUCH_INTERVAL = timedelta(hours=1)


def _hash(token: str) -> str:
    """The stored form of a token. Plain SHA-256, deliberately.

    No salt and no KDF: unlike a password this is 256 bits of uniform
    randomness, so there is no dictionary to attack and nothing for a slow
    hash to buy. What matters is that the stored value can't be replayed.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _as_utc(value: object) -> datetime | None:
    """Read a stored timestamp back as an aware UTC datetime.

    PyMongo hands back BSON dates as *naive* datetimes unless the client is
    built with `tz_aware=True`, and comparing one of those against
    `datetime.now(UTC)` raises TypeError rather than returning a wrong answer.
    Normalising on the way out keeps that from being a latent crash in the one
    code path that must never crash.
    """
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def create_session(user_id: str, *, user_agent: str | None = None) -> str:
    """Start a session and return the raw token. It is never recoverable again."""
    settings = get_settings()
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(_TOKEN_BYTES)

    await get_sessions_collection().insert_one(
        {
            "_id": _hash(token),
            "user_id": user_id,
            "created_at": now,
            "last_seen_at": now,
            "expires_at": now + timedelta(seconds=settings.session_idle_seconds),
            "absolute_expires_at": now + timedelta(seconds=settings.session_absolute_seconds),
            # Only to help someone recognise a session on the "signed in
            # devices" list. Truncated, because it's attacker-controlled text.
            "user_agent": (user_agent or "")[:200] or None,
        }
    )
    return token


async def resolve_session(token: str) -> str | None:
    """The user a token authenticates, or None if it isn't valid any more.

    Expired sessions are deleted as they're found, so the common case cleans
    up after itself and the TTL sweep only has to handle tokens nobody ever
    presents again.
    """
    key = _hash(token)
    doc = await get_sessions_collection().find_one({"_id": key})
    if doc is None:
        return None

    now = datetime.now(UTC)
    idle_deadline = _as_utc(doc.get("expires_at"))
    absolute_deadline = _as_utc(doc.get("absolute_expires_at"))

    # A row missing either deadline is malformed — treat it as expired rather
    # than as immortal.
    if idle_deadline is None or absolute_deadline is None:
        await get_sessions_collection().delete_one({"_id": key})
        return None

    if now >= idle_deadline or now >= absolute_deadline:
        await get_sessions_collection().delete_one({"_id": key})
        return None

    user_id = doc.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return None

    await _touch(key, now, idle_deadline, absolute_deadline)
    return user_id


async def _touch(
    key: str, now: datetime, idle_deadline: datetime, absolute_deadline: datetime
) -> None:
    """Slide the idle deadline forward, at most once per `_TOUCH_INTERVAL`.

    The idle window never extends past the absolute deadline, which is what
    keeps the absolute one absolute.
    """
    settings = get_settings()
    next_deadline = min(now + timedelta(seconds=settings.session_idle_seconds), absolute_deadline)
    if next_deadline - idle_deadline < _TOUCH_INTERVAL:
        return
    await get_sessions_collection().update_one(
        {"_id": key}, {"$set": {"last_seen_at": now, "expires_at": next_deadline}}
    )


async def revoke_session(token: str) -> None:
    """End one session — an ordinary sign-out."""
    await get_sessions_collection().delete_one({"_id": _hash(token)})


async def revoke_all_sessions(user_id: str, *, except_token: str | None = None) -> int:
    """End every session for a user. Returns how many were ended.

    `except_token` keeps the caller signed in where that reads better — a
    password change shouldn't sign you out of the tab you changed it in, even
    though it must sign out everyone else.
    """
    query: dict[str, object] = {"user_id": user_id}
    if except_token is not None:
        query["_id"] = {"$ne": _hash(except_token)}
    result = await get_sessions_collection().delete_many(query)
    return int(result.deleted_count)


async def list_sessions(user_id: str) -> list[dict[str, object]]:
    """A user's live sessions, for a "signed in devices" view.

    Returns no identifiers that could be replayed — the stored `_id` is the
    token hash, and handing that out would let a caller revoke sessions by
    guessing. Callers get what a human needs to recognise a device.
    """
    now = datetime.now(UTC)
    out: list[dict[str, object]] = []
    async for doc in get_sessions_collection().find({"user_id": user_id}):
        expires_at = _as_utc(doc.get("expires_at"))
        if expires_at is None or now >= expires_at:
            continue
        out.append(
            {
                "created_at": _as_utc(doc.get("created_at")),
                "last_seen_at": _as_utc(doc.get("last_seen_at")),
                "user_agent": doc.get("user_agent"),
            }
        )
    return out
