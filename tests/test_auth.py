from datetime import UTC, datetime, timedelta

import pytest

from short_timer.auth import check_passcode
from short_timer.config import get_settings
from short_timer.db import get_sessions_collection
from short_timer.sessions import (
    _hash,
    create_session,
    resolve_session,
    revoke_all_sessions,
    revoke_session,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_check_passcode() -> None:
    assert check_passcode("test-passcode") is True
    assert check_passcode("wrong") is False


# --- The cookie's name -------------------------------------------------------
# Production runs with `secure=true`, which the test suite does not, so the
# name used in production is only ever exercised here. Getting it wrong 401s
# every request, because the cookie is set under one name and read under
# another.


def test_cookie_takes_the_host_prefix_when_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    from short_timer.auth import _cookie_name

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    assert _cookie_name() == "__Host-short_timer_session"


def test_cookie_drops_the_prefix_for_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """`__Host-` mandates Secure, which local http dev can't set."""
    from short_timer.auth import _cookie_name

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    assert _cookie_name() == "short_timer_session"


# --- Sessions ----------------------------------------------------------------


async def test_session_round_trip() -> None:
    token = await create_session("someone")
    assert await resolve_session(token) == "someone"


async def test_garbage_token_resolves_to_nobody() -> None:
    assert await resolve_session("not-a-real-token") is None


async def test_token_is_never_stored_in_the_clear() -> None:
    """A dump of the sessions collection must not be replayable."""
    token = await create_session("someone")

    doc = await get_sessions_collection().find_one({"user_id": "someone"})
    assert doc is not None
    assert doc["_id"] != token
    assert doc["_id"] == _hash(token)
    # The raw token appears nowhere in the stored document.
    assert token not in str(doc)


async def test_revoked_session_stops_working() -> None:
    token = await create_session("someone")
    await revoke_session(token)
    assert await resolve_session(token) is None


async def test_revoke_all_ends_every_session_for_that_user() -> None:
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
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": _hash(token)},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    assert await resolve_session(token) is None


async def test_absolute_expiry_beats_a_fresh_idle_window() -> None:
    """A session used constantly still ends when the absolute deadline passes."""
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": _hash(token)},
        {
            "$set": {
                "expires_at": datetime.now(UTC) + timedelta(days=30),
                "absolute_expires_at": datetime.now(UTC) - timedelta(seconds=1),
            }
        },
    )
    assert await resolve_session(token) is None


async def test_expired_session_is_deleted_when_found() -> None:
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": _hash(token)},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )

    await resolve_session(token)
    assert await get_sessions_collection().find_one({"_id": _hash(token)}) is None


async def test_naive_stored_timestamps_are_read_as_utc() -> None:
    """PyMongo hands back naive datetimes; comparing those must not blow up."""
    token = await create_session("someone")
    await get_sessions_collection().update_one(
        {"_id": _hash(token)},
        # No tzinfo, as a real driver read would produce.
        {"$set": {"expires_at": (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)}},
    )
    assert await resolve_session(token) == "someone"


async def test_idle_deadline_slides_forward_on_use() -> None:
    token = await create_session("someone")
    # Push the stored deadline back far enough that a touch is worth a write.
    stale = datetime.now(UTC) + timedelta(days=1)
    await get_sessions_collection().update_one(
        {"_id": _hash(token)}, {"$set": {"expires_at": stale}}
    )

    assert await resolve_session(token) == "someone"

    doc = await get_sessions_collection().find_one({"_id": _hash(token)})
    assert doc is not None
    refreshed = doc["expires_at"]
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=UTC)
    assert refreshed > stale


async def test_idle_deadline_never_slides_past_the_absolute_one() -> None:
    token = await create_session("someone")
    cap = datetime.now(UTC) + timedelta(hours=2)
    await get_sessions_collection().update_one(
        {"_id": _hash(token)},
        {
            "$set": {
                "expires_at": datetime.now(UTC) + timedelta(minutes=1),
                "absolute_expires_at": cap,
            }
        },
    )

    assert await resolve_session(token) == "someone"

    doc = await get_sessions_collection().find_one({"_id": _hash(token)})
    assert doc is not None
    refreshed = doc["expires_at"]
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=UTC)
    assert refreshed <= cap
