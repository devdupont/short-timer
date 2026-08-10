"""Invite administration, and who is allowed to reach it."""

import pytest
import respx
from conftest import TEST_EMAIL
from httpx import ASGITransport, AsyncClient, Response

from shortimer.app import app
from shortimer.auth import invites
from shortimer.cache.db import get_users_collection
from shortimer.config import get_settings
from shortimer.model.status import Role
from shortimer.model.user import User

POSTMARK = "https://api.postmarkapp.com/email"


# --- Access ------------------------------------------------------------------


async def test_admin_routes_need_a_session(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/invites")).status_code == 401


async def test_an_ordinary_user_gets_404_not_403(authed_client: AsyncClient) -> None:
    """Don't confirm an endpoint exists to someone who may not use it."""
    assert (await authed_client.get("/api/admin/invites")).status_code == 404
    assert (await authed_client.post("/api/admin/invites", json={})).status_code == 404
    assert (await authed_client.get("/api/admin/users")).status_code == 404


async def test_staff_cannot_administer_accounts(authed_client: AsyncClient, account: User) -> None:
    """Staff read the privileged metrics; minting invites is a different power."""
    await get_users_collection().update_one(
        {"_id": account.id}, {"$set": {"role": Role.STAFF.value}}
    )
    assert (await authed_client.get("/api/admin/invites")).status_code == 404


async def test_an_admin_can_reach_them(admin_client: AsyncClient) -> None:
    assert (await admin_client.get("/api/admin/invites")).status_code == 200


# --- Creating invites ---------------------------------------------------------


async def test_creating_an_invite_returns_the_token_exactly_once(
    admin_client: AsyncClient,
) -> None:
    """It's stored hashed, so this response is the only chance to see it."""
    response = await admin_client.post("/api/admin/invites", json={"email": "invitee@example.com"})
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["token"] in body["link"]
    assert body["invite"]["email"] == "invitee@example.com"

    # The listing never carries it again.
    listed = (await admin_client.get("/api/admin/invites")).json()
    assert body["token"] not in str(listed)


async def test_an_open_invite_has_no_address(admin_client: AsyncClient) -> None:
    body = (await admin_client.post("/api/admin/invites", json={})).json()
    assert body["invite"]["email"] is None
    assert body["emailed"] is False


async def test_an_invite_can_name_a_role(admin_client: AsyncClient) -> None:
    body = (await admin_client.post("/api/admin/invites", json={"role": "staff"})).json()
    assert body["invite"]["role"] == "staff"


async def test_invite_addresses_are_normalised(admin_client: AsyncClient) -> None:
    body = (
        await admin_client.post("/api/admin/invites", json={"email": "Mixed@Example.COM"})
    ).json()
    assert body["invite"]["email"] == "mixed@example.com"


# --- Listing and revoking -----------------------------------------------------


async def test_revoking_an_unused_invite_removes_it(
    admin_client: AsyncClient, admin_account: User
) -> None:
    token, invite = await invites.create_invite(created_by=admin_account.id)

    assert (await admin_client.delete(f"/api/admin/invites/{invite.id}")).status_code == 204
    assert await invites.find_invite(token) is None


async def test_a_redeemed_invite_is_kept_as_a_record(
    admin_client: AsyncClient, admin_account: User
) -> None:
    token, invite = await invites.create_invite(created_by=admin_account.id)

    # A separate client, so redeeming doesn't replace the admin's session
    # cookie with the newcomer's — `client` and `admin_client` are one object.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as newcomer:
        await newcomer.post(
            "/api/auth/register",
            json={
                "invite_token": token,
                "email": "newcomer@example.com",
                "password": "a-long-enough-passphrase",
            },
        )

    # Revoking it is refused; it now records how an account came to exist.
    assert (await admin_client.delete(f"/api/admin/invites/{invite.id}")).status_code == 404

    [listed] = (await admin_client.get("/api/admin/invites")).json()
    assert listed["redeemed_at"] is not None
    assert listed["redeemed_by"] is not None


async def test_revoking_an_unknown_invite_is_404(admin_client: AsyncClient) -> None:
    assert (await admin_client.delete("/api/admin/invites/nope")).status_code == 404


# --- The user list ------------------------------------------------------------


async def test_the_user_list_never_carries_a_password_hash(
    admin_client: AsyncClient, account: User
) -> None:
    response = await admin_client.get("/api/admin/users")
    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert "argon2" not in response.text

    row = next(r for r in response.json() if r["email"] == TEST_EMAIL)
    # It reports *that* a password is set, which is all an admin needs.
    assert row["has_password"] is True


# --- Emailing the invite ------------------------------------------------------


@respx.mock
async def test_an_addressed_invite_is_emailed(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "test-token")
    get_settings.cache_clear()
    route = respx.post(POSTMARK).mock(return_value=Response(200, json={"MessageID": "1"}))

    body = (
        await admin_client.post("/api/admin/invites", json={"email": "invitee@example.com"})
    ).json()

    assert route.called
    assert body["emailed"] is True
    sent = route.calls.last.request
    assert sent.headers["X-Postmark-Server-Token"] == "test-token"


@respx.mock
async def test_a_failed_send_still_returns_the_link(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invite exists; the admin can pass the link on by hand."""
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "test-token")
    get_settings.cache_clear()
    respx.post(POSTMARK).mock(return_value=Response(422, json={"ErrorCode": 300}))

    response = await admin_client.post("/api/admin/invites", json={"email": "invitee@example.com"})
    assert response.status_code == 200
    body = response.json()
    assert body["emailed"] is False
    assert body["token"] in body["link"]
