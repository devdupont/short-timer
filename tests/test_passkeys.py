"""Passkey ceremonies: challenges, verification, and what the routes expose.

The cryptography itself is py_webauthn's job and is tested there. What's ours
is the plumbing around it — that a challenge is single-use, that it's bound to
the account that asked for it, that a verified assertion resolves to the right
user, and that one account's passkeys are invisible to another. Those are
tested by faking the verification result, which is the only way to exercise
them without a real authenticator.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_PASSWORD
from httpx import AsyncClient

from short_timer import passkeys
from short_timer.config import get_settings
from short_timer.db import get_credentials_collection, get_webauthn_challenges_collection
from short_timer.models import User


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass
class FakeRegistration:
    credential_id: bytes = b"credential-one"
    credential_public_key: bytes = b"public-key-bytes"
    sign_count: int = 0
    aaguid: str = "00000000-0000-0000-0000-000000000000"
    credential_backed_up: bool = True


@dataclass
class FakeAuthentication:
    new_sign_count: int = 1
    credential_backed_up: bool = True


def _credential(raw_id: str = "Y3JlZGVudGlhbC1vbmU") -> dict:
    """The shape the browser posts back. Contents are irrelevant when the
    verification itself is stubbed; only `rawId` is read by our code."""
    return {"id": raw_id, "rawId": raw_id, "response": {}, "type": "public-key"}


async def _register(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, credential_id: bytes = b"credential-one"
) -> dict:
    """Run a full registration with verification stubbed out."""
    monkeypatch.setattr(
        passkeys.webauthn,
        "verify_registration_response",
        lambda **_: FakeRegistration(credential_id=credential_id),
    )
    started = (await client.post("/api/me/passkeys/challenge")).json()
    response = await client.post(
        "/api/me/passkeys",
        json={
            "challenge_handle": started["challenge_handle"],
            "credential": _credential(),
            "nickname": "My phone",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- Challenges ---------------------------------------------------------------


async def test_a_challenge_is_stored_and_handed_out_by_handle(
    authed_client: AsyncClient,
) -> None:
    body = (await authed_client.post("/api/me/passkeys/challenge")).json()
    assert body["challenge_handle"]
    # The options go straight to navigator.credentials.create().
    assert body["options"]["rp"]["id"] == get_settings().webauthn_rp_id
    assert body["options"]["challenge"]

    # The handle is not the challenge, so a client can't choose what it signs.
    stored = [d async for d in get_webauthn_challenges_collection().find({})]
    assert len(stored) == 1
    assert body["challenge_handle"] not in str(stored)


async def test_registration_asks_for_a_discoverable_credential(
    authed_client: AsyncClient,
) -> None:
    """Without a resident key, signing in would need an email typed first."""
    body = (await authed_client.post("/api/me/passkeys/challenge")).json()
    assert body["options"]["authenticatorSelection"]["residentKey"] == "required"


async def test_login_options_name_no_credentials(client: AsyncClient) -> None:
    """An empty allowCredentials is what lets the browser offer any passkey —
    and is why this endpoint tells an attacker nothing about who has an account."""
    body = (await client.post("/api/auth/passkey/challenge")).json()
    assert not body["options"].get("allowCredentials")


async def test_a_challenge_cannot_be_spent_twice(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        passkeys.webauthn, "verify_registration_response", lambda **_: FakeRegistration()
    )
    started = (await authed_client.post("/api/me/passkeys/challenge")).json()
    payload = {
        "challenge_handle": started["challenge_handle"],
        "credential": _credential(),
        "nickname": "First",
    }

    assert (await authed_client.post("/api/me/passkeys", json=payload)).status_code == 200
    # Replaying the same challenge is refused.
    assert (await authed_client.post("/api/me/passkeys", json=payload)).status_code == 400


async def test_an_expired_challenge_is_refused(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expiry is checked in code; the TTL index sweeps far too slowly."""
    monkeypatch.setattr(
        passkeys.webauthn, "verify_registration_response", lambda **_: FakeRegistration()
    )
    started = (await authed_client.post("/api/me/passkeys/challenge")).json()
    await get_webauthn_challenges_collection().update_one(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
    )

    response = await authed_client.post(
        "/api/me/passkeys",
        json={
            "challenge_handle": started["challenge_handle"],
            "credential": _credential(),
            "nickname": "Late",
        },
    )
    assert response.status_code == 400


async def test_a_challenge_is_bound_to_the_account_that_asked(
    authed_client: AsyncClient, admin_account: User, sign_in_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise one account could finish another's registration."""
    monkeypatch.setattr(
        passkeys.webauthn, "verify_registration_response", lambda **_: FakeRegistration()
    )
    started = (await authed_client.post("/api/me/passkeys/challenge")).json()

    # Become somebody else, then try to finish it.
    await sign_in_as(authed_client, admin_account.id)
    response = await authed_client.post(
        "/api/me/passkeys",
        json={
            "challenge_handle": started["challenge_handle"],
            "credential": _credential(),
            "nickname": "Stolen",
        },
    )
    assert response.status_code == 400
    assert await get_credentials_collection().count_documents({}) == 0


# --- Registration -------------------------------------------------------------


async def test_registering_stores_the_credential(
    authed_client: AsyncClient, account: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = await _register(authed_client, monkeypatch)
    assert body["nickname"] == "My phone"
    assert body["backed_up"] is True

    doc = await get_credentials_collection().find_one({"_id": body["id"]})
    assert doc is not None
    assert doc["user_id"] == account.id
    assert doc["public_key"] == b"public-key-bytes"


async def test_the_public_key_never_leaves_the_server(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a secret exactly, but there's no reason for a client to hold it."""
    await _register(authed_client, monkeypatch)
    listed = await authed_client.get("/api/me/passkeys")
    assert "public_key" not in listed.text


async def test_registering_requires_a_session(client: AsyncClient) -> None:
    assert (await client.post("/api/me/passkeys/challenge")).status_code == 401
    assert (await client.get("/api/me/passkeys")).status_code == 401


async def test_a_second_registration_excludes_the_first(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stops one device quietly registering itself twice."""
    await _register(authed_client, monkeypatch)
    body = (await authed_client.post("/api/me/passkeys/challenge")).json()
    assert len(body["options"]["excludeCredentials"]) == 1


# --- Authentication -----------------------------------------------------------


async def test_a_verified_assertion_signs_the_owner_in(
    client: AsyncClient, account: User, sign_in_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    await sign_in_as(client, account.id)
    registered = await _register(client, monkeypatch)
    await client.post("/api/auth/logout")
    assert (await client.get("/api/me")).status_code == 401

    monkeypatch.setattr(
        passkeys.webauthn,
        "verify_authentication_response",
        lambda **_: FakeAuthentication(),
    )
    started = (await client.post("/api/auth/passkey/challenge")).json()
    response = await client.post(
        "/api/auth/passkey/login",
        json={
            "challenge_handle": started["challenge_handle"],
            "credential": _credential(registered["id"]),
        },
    )
    assert response.status_code == 204
    # …and the session it minted belongs to the credential's owner.
    assert (await client.get("/api/me")).json()["id"] == account.id


async def test_an_unregistered_credential_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = (await client.post("/api/auth/passkey/challenge")).json()
    response = await client.post(
        "/api/auth/passkey/login",
        json={
            "challenge_handle": started["challenge_handle"],
            "credential": _credential("never-registered"),
        },
    )
    assert response.status_code == 401


async def test_the_sign_counter_is_advanced(
    client: AsyncClient, account: User, sign_in_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storing it is what keeps the clone check meaningful next time."""
    await sign_in_as(client, account.id)
    registered = await _register(client, monkeypatch)
    await client.post("/api/auth/logout")

    monkeypatch.setattr(
        passkeys.webauthn,
        "verify_authentication_response",
        lambda **_: FakeAuthentication(new_sign_count=42),
    )
    started = (await client.post("/api/auth/passkey/challenge")).json()
    await client.post(
        "/api/auth/passkey/login",
        json={
            "challenge_handle": started["challenge_handle"],
            "credential": _credential(registered["id"]),
        },
    )

    doc = await get_credentials_collection().find_one({"_id": registered["id"]})
    assert doc is not None
    assert doc["sign_count"] == 42
    assert doc["last_used_at"] is not None


async def test_a_disabled_account_cannot_sign_in_with_a_passkey(
    client: AsyncClient, account: User, sign_in_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    from short_timer.db import get_users_collection

    await sign_in_as(client, account.id)
    registered = await _register(client, monkeypatch)
    await client.post("/api/auth/logout")
    await get_users_collection().update_one({"_id": account.id}, {"$set": {"status": "disabled"}})

    monkeypatch.setattr(
        passkeys.webauthn, "verify_authentication_response", lambda **_: FakeAuthentication()
    )
    started = (await client.post("/api/auth/passkey/challenge")).json()
    response = await client.post(
        "/api/auth/passkey/login",
        json={
            "challenge_handle": started["challenge_handle"],
            "credential": _credential(registered["id"]),
        },
    )
    assert response.status_code == 403


# --- Managing them ------------------------------------------------------------


async def test_deleting_a_passkey_removes_it(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    registered = await _register(authed_client, monkeypatch)
    assert (await authed_client.delete(f"/api/me/passkeys/{registered['id']}")).status_code == 204
    assert await get_credentials_collection().count_documents({}) == 0


async def test_you_cannot_delete_someone_elses_passkey(
    client: AsyncClient, account: User, admin_account: User, sign_in_as, monkeypatch
) -> None:
    await sign_in_as(client, account.id)
    registered = await _register(client, monkeypatch)

    await sign_in_as(client, admin_account.id)
    assert (await client.delete(f"/api/me/passkeys/{registered['id']}")).status_code == 404
    assert await get_credentials_collection().count_documents({}) == 1


async def test_the_listing_is_scoped_to_the_caller(
    client: AsyncClient, account: User, admin_account: User, sign_in_as, monkeypatch
) -> None:
    await sign_in_as(client, account.id)
    await _register(client, monkeypatch)

    await sign_in_as(client, admin_account.id)
    assert (await client.get("/api/me/passkeys")).json() == []


# --- Configuration ------------------------------------------------------------


def test_the_rp_id_is_the_apex_not_the_api_subdomain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one decision here that can never be undone.

    A credential registered at the apex works from any subdomain; one
    registered at api.shortimer.com could never be used at the apex. The value
    is hashed into the authenticator at creation.
    """
    monkeypatch.setenv("WEBAUTHN_RP_ID", "shortimer.com")
    monkeypatch.setenv("WEBAUTHN_ORIGINS", "https://shortimer.com")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.webauthn_rp_id == "shortimer.com"
    # The origin is the *site*; the RP ID is a registrable suffix of it, which
    # is what makes verifying on api.shortimer.com legal.
    assert settings.webauthn_origins == ["https://shortimer.com"]
    assert all(origin.endswith(settings.webauthn_rp_id) for origin in settings.webauthn_origins)


async def test_registering_a_passkey_does_not_replace_the_password(
    authed_client: AsyncClient, account: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passkey on a lost phone still needs a way back in."""
    await _register(authed_client, monkeypatch)

    from short_timer.users import get_user

    user = await get_user(account.id)
    assert user is not None and user.password_hash is not None
    # …and the password still signs you in.
    await authed_client.post("/api/auth/logout")
    assert (
        await authed_client.post(
            "/api/auth/login",
            json={"email": user.email, "password": TEST_PASSWORD},
        )
    ).status_code == 204
