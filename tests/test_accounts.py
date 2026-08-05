"""Registration, sign-in, verification and password reset, end to end."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_EMAIL, TEST_PASSWORD
from httpx import AsyncClient

from short_timer import invites
from short_timer.config import get_settings
from short_timer.db import (
    get_email_tokens_collection,
    get_invites_collection,
    get_sessions_collection,
    get_users_collection,
)
from short_timer.email_tokens import TokenKind
from short_timer.models import Role, User
from short_timer.sessions import create_session, resolve_session
from short_timer.users import get_user, get_user_by_email

NEW_EMAIL = "newcomer@example.com"
NEW_PASSWORD = "another-long-enough-passphrase"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _invite(admin: User, email: str | None = None, role: Role = Role.USER) -> str:
    token, _ = await invites.create_invite(created_by=admin.id, email=email, role=role)
    return token


# --- Registration is invite-only ---------------------------------------------


async def test_registering_without_an_invite_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"invite_token": "made-up", "email": NEW_EMAIL, "password": NEW_PASSWORD},
    )
    assert response.status_code == 400
    assert await get_users_collection().count_documents({"email": NEW_EMAIL}) == 0


async def test_registering_with_an_invite_creates_an_account_and_signs_in(
    client: AsyncClient, admin_account: User
) -> None:
    token = await _invite(admin_account, NEW_EMAIL)

    response = await client.post(
        "/api/auth/register",
        json={
            "invite_token": token,
            "email": NEW_EMAIL,
            "password": NEW_PASSWORD,
            "display_name": "Newcomer",
        },
    )
    assert response.status_code == 204

    user = await get_user_by_email(NEW_EMAIL)
    assert user is not None
    assert user.display_name == "Newcomer"
    assert user.role is Role.USER
    # The response set a session cookie, so the client is already signed in.
    assert (await client.get("/api/me")).json()["id"] == user.id


async def test_an_emailed_invite_arrives_verified(client: AsyncClient, admin_account: User) -> None:
    """Delivery to the address already proved control of the mailbox."""
    token = await _invite(admin_account, NEW_EMAIL)
    await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": NEW_EMAIL, "password": NEW_PASSWORD},
    )

    user = await get_user_by_email(NEW_EMAIL)
    assert user is not None and user.email_verified is True
    # …so no confirmation email was needed.
    assert await get_email_tokens_collection().count_documents({"kind": "verify"}) == 0


async def test_an_open_code_still_has_to_verify(client: AsyncClient, admin_account: User) -> None:
    """A code proves nothing about the address typed into the form."""
    token = await _invite(admin_account, email=None)
    await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": NEW_EMAIL, "password": NEW_PASSWORD},
    )

    user = await get_user_by_email(NEW_EMAIL)
    assert user is not None and user.email_verified is False
    assert await get_email_tokens_collection().count_documents({"kind": "verify"}) == 1


async def test_a_bound_invite_refuses_a_different_address(
    client: AsyncClient, admin_account: User
) -> None:
    token = await _invite(admin_account, "invited@example.com")
    response = await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": "someone.else@example.com", "password": NEW_PASSWORD},
    )
    assert response.status_code == 400
    assert await get_users_collection().count_documents({"email": "someone.else@example.com"}) == 0


async def test_an_invite_works_only_once(client: AsyncClient, admin_account: User) -> None:
    token = await _invite(admin_account, email=None)
    first = await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": NEW_EMAIL, "password": NEW_PASSWORD},
    )
    assert first.status_code == 204

    second = await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": "third@example.com", "password": NEW_PASSWORD},
    )
    assert second.status_code == 400
    assert await get_users_collection().count_documents({"email": "third@example.com"}) == 0


async def test_an_expired_invite_is_refused(client: AsyncClient, admin_account: User) -> None:
    token = await _invite(admin_account, email=None)
    await get_invites_collection().update_one(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    response = await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": NEW_EMAIL, "password": NEW_PASSWORD},
    )
    assert response.status_code == 400


async def test_an_invite_can_grant_a_role(client: AsyncClient, admin_account: User) -> None:
    token = await _invite(admin_account, "staffer@example.com", role=Role.STAFF)
    await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": "staffer@example.com", "password": NEW_PASSWORD},
    )
    user = await get_user_by_email("staffer@example.com")
    assert user is not None and user.role is Role.STAFF


async def test_a_short_password_is_refused(client: AsyncClient, admin_account: User) -> None:
    token = await _invite(admin_account, email=None)
    response = await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": NEW_EMAIL, "password": "short"},
    )
    assert response.status_code == 422
    assert await get_users_collection().count_documents({"email": NEW_EMAIL}) == 0


async def test_a_taken_address_is_refused(
    client: AsyncClient, admin_account: User, account: User
) -> None:
    token = await _invite(admin_account, email=None)
    response = await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": TEST_EMAIL, "password": NEW_PASSWORD},
    )
    assert response.status_code == 409


async def test_addresses_are_matched_case_insensitively(
    client: AsyncClient, admin_account: User, account: User
) -> None:
    """Otherwise Me@example.com is a second account for the same person."""
    token = await _invite(admin_account, email=None)
    response = await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": TEST_EMAIL.upper(), "password": NEW_PASSWORD},
    )
    assert response.status_code == 409


# --- Checking an invite before showing the form -------------------------------


async def test_invite_check_reports_a_bound_address(
    client: AsyncClient, admin_account: User
) -> None:
    token = await _invite(admin_account, NEW_EMAIL)
    body = (await client.get("/api/auth/invite", params={"token": token})).json()
    assert body["valid"] is True
    assert body["email"] == NEW_EMAIL


async def test_invite_check_reports_an_open_code(client: AsyncClient, admin_account: User) -> None:
    token = await _invite(admin_account, email=None)
    body = (await client.get("/api/auth/invite", params={"token": token})).json()
    assert body["valid"] is True
    assert body["email"] is None


async def test_invite_check_rejects_a_bad_token(client: AsyncClient) -> None:
    body = (await client.get("/api/auth/invite", params={"token": "nope"})).json()
    assert body["valid"] is False
    assert body["reason"]


# --- Signing in ---------------------------------------------------------------


async def test_login_and_logout(client: AsyncClient, account: User) -> None:
    assert (
        await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    ).status_code == 204
    assert (await client.get("/api/me")).status_code == 200

    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/me")).status_code == 401


async def test_logout_destroys_the_session_server_side(client: AsyncClient, account: User) -> None:
    """Clearing the cookie alone would leave a working token in the database."""
    await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert await get_sessions_collection().count_documents({"user_id": account.id}) == 1

    await client.post("/api/auth/logout")
    assert await get_sessions_collection().count_documents({"user_id": account.id}) == 0


async def test_logout_all_ends_every_session(client: AsyncClient, account: User) -> None:
    elsewhere = await create_session(account.id)
    await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})

    assert (await client.post("/api/auth/logout-all")).status_code == 204
    assert await resolve_session(elsewhere) is None
    assert await get_sessions_collection().count_documents({"user_id": account.id}) == 0


async def test_a_disabled_account_cannot_sign_in(client: AsyncClient, account: User) -> None:
    await get_users_collection().update_one({"_id": account.id}, {"$set": {"status": "disabled"}})
    response = await client.post(
        "/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 403


async def test_login_upgrades_a_weak_hash(client: AsyncClient, account: User) -> None:
    """Raising the cost applies at next login, without a reset."""
    from argon2 import PasswordHasher

    weak = PasswordHasher(memory_cost=8 * 1024, time_cost=1, parallelism=1)
    await get_users_collection().update_one(
        {"_id": account.id}, {"$set": {"password_hash": weak.hash(TEST_PASSWORD)}}
    )

    assert (
        await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    ).status_code == 204

    user = await get_user(account.id)
    assert user is not None and user.password_hash is not None
    assert f"m={19 * 1024}" in user.password_hash


# --- Email verification -------------------------------------------------------


async def _token_of(kind: TokenKind) -> str | None:
    """Tokens are stored hashed, so a test can't read one back.

    Verification and reset are exercised through the module that mints them
    instead; this only confirms *a* token exists.
    """
    doc = await get_email_tokens_collection().find_one({"kind": kind.value})
    return doc["_id"] if doc else None


async def test_verification_marks_the_address_confirmed(
    client: AsyncClient, admin_account: User
) -> None:
    from short_timer import email_tokens

    token = await _invite(admin_account, email=None)
    await client.post(
        "/api/auth/register",
        json={"invite_token": token, "email": NEW_EMAIL, "password": NEW_PASSWORD},
    )
    user = await get_user_by_email(NEW_EMAIL)
    assert user is not None and user.email_verified is False

    # Re-issue so the test holds the raw token; the registration one is hashed.
    verify_token = await email_tokens.issue(TokenKind.VERIFY, user_id=user.id, email=NEW_EMAIL)
    response = await client.post("/api/auth/verify", json={"token": verify_token})
    assert response.status_code == 200
    assert response.json()["email_verified"] is True


async def test_a_verification_token_works_only_once(client: AsyncClient, account: User) -> None:
    from short_timer import email_tokens

    token = await email_tokens.issue(TokenKind.VERIFY, user_id=account.id, email=TEST_EMAIL)
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 200
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 400


async def test_an_expired_verification_token_is_refused(client: AsyncClient, account: User) -> None:
    from short_timer import email_tokens

    token = await email_tokens.issue(TokenKind.VERIFY, user_id=account.id, email=TEST_EMAIL)
    await get_email_tokens_collection().update_one(
        {"kind": "verify"}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 400


# --- Password reset -----------------------------------------------------------


async def test_forgot_password_says_nothing_about_who_has_an_account(
    client: AsyncClient, account: User
) -> None:
    known = await client.post("/api/auth/forgot-password", json={"email": TEST_EMAIL})
    unknown = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 204
    assert known.text == unknown.text
    # …but only the real one produced a token.
    assert await get_email_tokens_collection().count_documents({"kind": "reset"}) == 1


async def test_reset_sets_the_password_and_ends_every_session(
    client: AsyncClient, account: User
) -> None:
    from short_timer import email_tokens

    stale = await create_session(account.id)
    token = await email_tokens.issue(TokenKind.RESET, user_id=account.id, email=TEST_EMAIL)

    response = await client.post(
        "/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 204

    # Every session that existed before the reset is gone.
    assert await resolve_session(stale) is None
    # The new password works and the old one doesn't.
    assert (
        await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": NEW_PASSWORD})
    ).status_code == 204
    assert (
        await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    ).status_code == 401


async def test_issuing_a_reset_invalidates_the_previous_one(
    client: AsyncClient, account: User
) -> None:
    """Otherwise every "resend" leaves another live credential in an inbox."""
    from short_timer import email_tokens

    first = await email_tokens.issue(TokenKind.RESET, user_id=account.id, email=TEST_EMAIL)
    second = await email_tokens.issue(TokenKind.RESET, user_id=account.id, email=TEST_EMAIL)

    assert (
        await client.post(
            "/api/auth/reset-password", json={"token": first, "password": NEW_PASSWORD}
        )
    ).status_code == 400
    assert (
        await client.post(
            "/api/auth/reset-password", json={"token": second, "password": NEW_PASSWORD}
        )
    ).status_code == 204


async def test_an_expired_reset_token_is_refused(client: AsyncClient, account: User) -> None:
    from short_timer import email_tokens

    token = await email_tokens.issue(TokenKind.RESET, user_id=account.id, email=TEST_EMAIL)
    await get_email_tokens_collection().update_one(
        {"kind": "reset"}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    assert (
        await client.post(
            "/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
        )
    ).status_code == 400


async def test_reset_refuses_a_short_password(client: AsyncClient, account: User) -> None:
    from short_timer import email_tokens

    token = await email_tokens.issue(TokenKind.RESET, user_id=account.id, email=TEST_EMAIL)
    response = await client.post(
        "/api/auth/reset-password", json={"token": token, "password": "short"}
    )
    assert response.status_code == 422


# --- Changing a password while signed in --------------------------------------


async def test_changing_a_password_requires_the_current_one(
    authed_client: AsyncClient,
) -> None:
    response = await authed_client.post(
        "/api/me/password",
        json={"current_password": "not-it", "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 403


async def test_changing_a_password_keeps_this_session_and_drops_the_rest(
    authed_client: AsyncClient, account: User
) -> None:
    elsewhere = await create_session(account.id)

    response = await authed_client.post(
        "/api/me/password",
        json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204

    assert await resolve_session(elsewhere) is None
    # The caller is still signed in on the tab they used.
    assert (await authed_client.get("/api/me")).status_code == 200


# --- Tokens are never stored in a replayable form -----------------------------


async def test_no_token_collection_stores_a_usable_token(
    client: AsyncClient, admin_account: User, account: User
) -> None:
    from short_timer import email_tokens

    invite_token = await _invite(admin_account, email=None)
    reset_token = await email_tokens.issue(TokenKind.RESET, user_id=account.id, email=TEST_EMAIL)
    session_token = await create_session(account.id)

    invite_docs = [d async for d in get_invites_collection().find({})]
    token_docs = [d async for d in get_email_tokens_collection().find({})]
    session_docs = [d async for d in get_sessions_collection().find({})]

    for raw, docs in (
        (invite_token, invite_docs),
        (reset_token, token_docs),
        (session_token, session_docs),
    ):
        assert raw not in str(docs)
