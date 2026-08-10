from collections.abc import Awaitable, Callable, Generator
from typing import Any

import pytest
from httpx import AsyncClient

from shortimer.cache import crypto
from shortimer.cache.crypto import decrypt, generate_key
from shortimer.cache.db import get_users_collection
from shortimer.config import get_settings
from shortimer.model.gym import GymProvider
from shortimer.model.user import User
from shortimer.users import get_user


@pytest.fixture(autouse=True)
def _secrets_configured(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Most of these tests store credentials, which needs an encryption key."""
    monkeypatch.setenv("SECRETS_KEYS", generate_key())
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._cipher.cache_clear()


# Session mechanics moved to the database; they're covered in test_auth.py.


# --- /api/me -----------------------------------------------------------------


async def test_me_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/me")).status_code == 401


def _connection(config: dict[str, Any], provider: str) -> dict[str, Any] | None:
    """One provider's stored connection out of the config payload.

    `gyms` is a list rather than a map because it only ever holds the providers
    a user actually configured — an unconfigured one is absent, not an empty
    entry — so reads go through here instead of indexing by key.
    """
    return next((gym for gym in config["gyms"] if gym["provider"] == provider), None)


async def test_me_returns_empty_config_initially(authed_client: AsyncClient, account: User) -> None:
    response = await authed_client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == account.id
    assert body["secrets_available"] is True
    # No connections at all, rather than a set of blank ones.
    assert body["config"]["gyms"] == []


async def test_config_update_stores_and_masks_credential(authed_client: AsyncClient) -> None:
    response = await authed_client.put(
        "/api/me/config",
        json={
            "gyms": {
                "wodify_owner": {
                    "credential": "wodify-secret-key-9876",
                    "location": "Main",
                    "program": "CrossFit",
                    "enabled": True,
                }
            }
        },
    )
    assert response.status_code == 200
    owner = _connection(response.json()["config"], "wodify_owner")
    assert owner is not None
    assert owner["credential"] == {"is_set": True, "masked": "••••9876"}
    assert owner["location"] == "Main"
    assert owner["enabled"] is True


async def test_credential_is_never_returned_in_full(authed_client: AsyncClient) -> None:
    secret = "wodify-secret-key-9876"
    await authed_client.put(
        "/api/me/config", json={"gyms": {"wodify_owner": {"credential": secret}}}
    )
    response = await authed_client.get("/api/me")
    assert secret not in response.text


async def test_credential_is_encrypted_at_rest(authed_client: AsyncClient, account: User) -> None:
    secret = "wodify-secret-key-9876"
    await authed_client.put(
        "/api/me/config", json={"gyms": {"wodify_owner": {"credential": secret}}}
    )

    raw = await get_users_collection().find_one({"_id": account.id})
    assert raw is not None
    assert secret not in str(raw["config"])
    # …and the server can still read it back.
    user = await get_user(account.id)
    assert user is not None
    connection = user.config.connection(GymProvider.WODIFY_OWNER)
    assert connection is not None and connection.credential is not None
    assert decrypt(connection.credential) == secret


async def test_omitted_credential_is_left_alone(authed_client: AsyncClient) -> None:
    """Toggling `enabled` must not require re-entering the key."""
    await authed_client.put(
        "/api/me/config",
        json={"gyms": {"wodify_owner": {"credential": "wodify-secret-key-9876"}}},
    )
    response = await authed_client.put(
        "/api/me/config",
        json={"gyms": {"wodify_owner": {"enabled": True, "location": "Downtown"}}},
    )
    owner = _connection(response.json()["config"], "wodify_owner")
    assert owner is not None
    assert owner["credential"]["is_set"] is True
    assert owner["credential"]["masked"] == "••••9876"
    assert owner["enabled"] is True
    assert owner["location"] == "Downtown"


async def test_empty_string_clears_credential(authed_client: AsyncClient) -> None:
    await authed_client.put(
        "/api/me/config",
        json={"gyms": {"wodify_owner": {"credential": "wodify-secret-key-9876"}}},
    )
    response = await authed_client.put(
        "/api/me/config", json={"gyms": {"wodify_owner": {"credential": ""}}}
    )
    # Clearing the last thing worth keeping drops the connection entirely,
    # rather than leaving a husk the settings screen would call "connected".
    assert _connection(response.json()["config"], "wodify_owner") is None


async def test_clearing_a_credential_keeps_other_fields(authed_client: AsyncClient) -> None:
    """A connection with settings left on it survives losing its key."""
    await authed_client.put(
        "/api/me/config",
        json={"gyms": {"wodify_owner": {"credential": "key-1234", "location": "Main"}}},
    )
    response = await authed_client.put(
        "/api/me/config", json={"gyms": {"wodify_owner": {"credential": ""}}}
    )
    owner = _connection(response.json()["config"], "wodify_owner")
    assert owner is not None
    assert owner["credential"]["is_set"] is False
    assert owner["location"] == "Main"


async def test_untouched_provider_is_preserved(authed_client: AsyncClient) -> None:
    """Only the providers named in the request are touched."""
    await authed_client.put(
        "/api/me/config",
        json={"gyms": {"wodify_member": {"credential": "wb-key-abcd1234"}}},
    )
    response = await authed_client.put(
        "/api/me/config", json={"gyms": {"wodify_owner": {"location": "Main"}}}
    )
    config = response.json()["config"]
    member = _connection(config, "wodify_member")
    owner = _connection(config, "wodify_owner")
    assert member is not None and member["credential"]["is_set"] is True
    assert owner is not None and owner["location"] == "Main"


async def test_several_providers_can_be_saved_at_once(authed_client: AsyncClient) -> None:
    """The update is a map, so one request can configure two platforms."""
    response = await authed_client.put(
        "/api/me/config",
        json={
            "gyms": {
                "wodify_member": {"credential": "wb-key-abcd1234", "enabled": True},
                "sugarwod_owner": {"credential": "sugar-key-5678", "enabled": True},
            }
        },
    )
    config = response.json()["config"]
    assert {gym["provider"] for gym in config["gyms"]} == {"wodify_member", "sugarwod_owner"}


async def test_an_unknown_provider_is_rejected(authed_client: AsyncClient) -> None:
    """The provider set is closed; a typo shouldn't silently store nothing."""
    response = await authed_client.put(
        "/api/me/config", json={"gyms": {"not_a_platform": {"credential": "x"}}}
    )
    assert response.status_code == 422


async def test_config_is_scoped_to_the_session_user(
    authed_client: AsyncClient, sign_in_as: Callable[[AsyncClient, str], Awaitable[str]]
) -> None:
    """Another user's session must not see this user's credentials."""
    await authed_client.put(
        "/api/me/config",
        json={"gyms": {"wodify_owner": {"credential": "wodify-secret-key-9876"}}},
    )
    from shortimer.model.user import User

    other = User(id="someone-else", display_name="Other")
    doc = other.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    await get_users_collection().insert_one(doc)

    await sign_in_as(authed_client, "someone-else")
    response = await authed_client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "someone-else"
    assert body["config"]["gyms"] == []


async def test_saving_credential_without_keys_reports_unavailable(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECRETS_KEYS", "")
    get_settings.cache_clear()
    crypto._cipher.cache_clear()

    response = await authed_client.put(
        "/api/me/config",
        json={"gyms": {"wodify_owner": {"credential": "wodify-secret-key-9876"}}},
    )
    assert response.status_code == 503
    assert (await authed_client.get("/api/me")).json()["secrets_available"] is False


async def test_non_credential_fields_save_without_keys(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment with no keys can still be configured, just not with secrets."""
    monkeypatch.setenv("SECRETS_KEYS", "")
    get_settings.cache_clear()
    crypto._cipher.cache_clear()

    response = await authed_client.put(
        "/api/me/config", json={"gyms": {"wodify_owner": {"location": "Main", "enabled": True}}}
    )
    assert response.status_code == 200
    owner = _connection(response.json()["config"], "wodify_owner")
    assert owner is not None and owner["location"] == "Main"
