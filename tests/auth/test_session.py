"""The session cookie's name, and server-side session lifecycle: creation, expiry, revocation."""

from datetime import UTC, datetime, timedelta

import pytest

from shortimer.auth.tokens import hash_token
from shortimer.cache.db import get_sessions_collection
from shortimer.cache.session import (
    create_session,
    list_sessions,
    resolve_session,
    revoke_all_sessions,
    revoke_session,
)
from shortimer.config import get_settings

# --- The cookie's name -------------------------------------------------------
# Production runs with `secure=true`, which the test suite does not, so the
# name used in production is only ever exercised here. Getting it wrong 401s
# every request, because the cookie is set under one name and read under
# another.


def test_cookie_takes_the_host_prefix_when_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secure deployment names its cookie `__Host-shortimer_session`."""
    from shortimer.auth.session import _cookie_name

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    assert _cookie_name() == "__Host-shortimer_session"


def test_cookie_drops_the_prefix_for_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """`__Host-` mandates Secure, which local http dev can't set."""
    from shortimer.auth.session import _cookie_name

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    assert _cookie_name() == "shortimer_session"


# --- Sessions ----------------------------------------------------------------


async def test_session_round_trip() -> None:
    """A freshly created session's token resolves back to the user it was created for."""
    token = await create_session("someone")
    assert await resolve_session(token) == "someone"


async def test_garbage_token_resolves_to_nobody() -> None:
    """A string that was never issued as a token resolves to None."""
    assert await resolve_session("not-a-real-token") is None


async def test_token_is_never_stored_in_the_clear() -> None:
    """A dump of the sessions collection must not be replayable."""
    token = await create_session("someone")

    doc = await get_sessions_collection().find_one({"user_id": "someone"})
    assert doc is not None
    assert doc["_id"] != token
    assert doc["_id"] == hash_token(token)
    # The raw token appears nowhere in the stored document.
    assert token not in str(doc)


async def test_revoked_session_stops_working() -> None:
    """A revoked token no longer resolves to anyone."""
    token = await create_session("someone")
    await revoke_session(token)
    assert await resolve_session(token) is None


async def test_revoke_all_ends_every_session_for_that_user() -> None:
    """Revoking all of one user's sessions ends only theirs, not another user's."""
    mine = [await create_session("me") for _ in range(3)]
    theirs = await create_session("someone-else")

    assert await revoke_all_sessions("me") == 3

    for token in mine:
        assert await resolve_session(token) is None
    assert await resolve_session(theirs) == "someone-else"


async def test_revoke_all_can_spare_the_current_session() -> None:
    """Changing a password shouldn't sign you out of the tab you changed it in."""
    keep = await create_session("me")
    drop = await create_session("me")

    assert await revoke_all_sessions("me", except_token=keep) == 1
    assert await resolve_session(keep) == "me"
    assert await resolve_session(drop) is None


# --- Expiry is enforced in code, not by the TTL index ------------------------
# mongomock never runs a TTL sweep, and real Mongo's runs about once a minute.
# If these passed only because of the index they would be testing nothing.


async def test_idle_expiry_is_enforced_on_read() -> None:
    """A session past its idle deadline is rejected in code, not left to the TTL index."""
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    assert await resolve_session(token) is None


async def test_absolute_expiry_beats_a_fresh_idle_window() -> None:
    """A session used constantly still ends when the absolute deadline passes."""
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)},
        {
            "$set": {
                "expires_at": datetime.now(UTC) + timedelta(days=30),
                "absolute_expires_at": datetime.now(UTC) - timedelta(seconds=1),
            }
        },
    )
    assert await resolve_session(token) is None


async def test_expired_session_is_deleted_when_found() -> None:
    """Resolving an expired session deletes its row rather than leaving it for the TTL sweep."""
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    await resolve_session(token)
    assert await get_sessions_collection().find_one({"_id": hash_token(token)}) is None


async def test_naive_stored_timestamps_are_read_as_utc() -> None:
    """PyMongo hands back naive datetimes; comparing those must not blow up."""
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)},
        # No tzinfo, as a real driver read would produce.
        {"$set": {"expires_at": (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)}},
    )
    assert await resolve_session(token) == "someone"


async def test_idle_deadline_slides_forward_on_use() -> None:
    """Resolving a session with a stale idle deadline pushes it forward."""
    token = await create_session("someone")
    # Push the stored deadline back far enough that a touch is worth a write.
    stale = datetime.now(UTC) + timedelta(days=1)
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)}, {"$set": {"expires_at": stale}}
    )

    assert await resolve_session(token) == "someone"

    doc = await get_sessions_collection().find_one({"_id": hash_token(token)})
    assert doc is not None
    refreshed = doc["expires_at"]
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=UTC)
    assert refreshed > stale


async def test_idle_deadline_never_slides_past_the_absolute_one() -> None:
    """The idle deadline, when it slides, is capped at the session's absolute deadline."""
    token = await create_session("someone")
    cap = datetime.now(UTC) + timedelta(hours=2)
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)},
        {
            "$set": {
                "expires_at": datetime.now(UTC) + timedelta(minutes=1),
                "absolute_expires_at": cap,
            }
        },
    )

    assert await resolve_session(token) == "someone"

    doc = await get_sessions_collection().find_one({"_id": hash_token(token)})
    assert doc is not None
    refreshed = doc["expires_at"]
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=UTC)
    assert refreshed <= cap


# --- The "signed in devices" list --------------------------------------------


async def test_listing_sessions_returns_nothing_replayable() -> None:
    """The screen shows where you're signed in; it must not hand out the keys.

    The stored `_id` *is* the token hash, so including it — or anything derived
    from it — would let whoever read the list revoke sessions, or worse, by an
    identifier they were never supposed to hold.
    """
    token = await create_session("someone", user_agent="Firefox")

    rows = await list_sessions("someone")

    assert len(rows) == 1
    assert set(rows[0]) == {"created_at", "last_seen_at", "user_agent"}
    assert token not in str(rows)
    assert hash_token(token) not in str(rows)


async def test_listing_sessions_only_shows_that_user() -> None:
    """One person's devices are not another's."""
    await create_session("someone", user_agent="Firefox")
    await create_session("somebody-else", user_agent="Safari")

    rows = await list_sessions("someone")

    assert [row["user_agent"] for row in rows] == ["Firefox"]


async def test_listing_sessions_hides_expired_ones() -> None:
    """An expired session is already dead; showing it invites pointless revoking.

    Expiry is enforced on read rather than by a sweep (see the index comments
    in `db.py`), so the row can still be sitting there when the list is drawn.
    """
    token = await create_session("someone", user_agent="Firefox")
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    assert await list_sessions("someone") == []


async def test_listing_sessions_hides_a_row_with_no_expiry_at_all() -> None:
    """A document missing `expires_at` can't be shown to be live, so it isn't.

    Nothing writes such a row today. It's the safe reading of a malformed one:
    treating "no deadline" as "never expires" would make a corrupt document the
    most durable session in the database.
    """
    token = await create_session("someone", user_agent="Firefox")
    await get_sessions_collection().update_one(
        {"_id": hash_token(token)}, {"$unset": {"expires_at": ""}}
    )

    assert await list_sessions("someone") == []


async def test_listing_sessions_is_empty_for_someone_with_none() -> None:
    """No sessions is an empty list, not an error."""
    assert await list_sessions("nobody") == []
