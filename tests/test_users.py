import pytest
from httpx import ASGITransport, AsyncClient

from short_timer import crypto
from short_timer.app import app
from short_timer.auth import DEFAULT_OWNER_ID, create_session_token, session_user_id
from short_timer.config import get_settings
from short_timer.crypto import decrypt, generate_key
from short_timer.db import get_users_collection, get_workouts_collection
from short_timer.models import Workout, WorkoutMode
from short_timer.users import ensure_default_user, get_user


@pytest.fixture(autouse=True)
def _secrets_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most of these tests store credentials, which needs an encryption key."""
    monkeypatch.setenv("SECRETS_KEYS", generate_key())
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._cipher.cache_clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def authed_client(client: AsyncClient) -> AsyncClient:
    response = await client.post("/api/auth/login", json={"passcode": "test-passcode"})
    assert response.status_code == 204
    return client


# --- Sessions carry a user ---------------------------------------------------


def test_session_token_carries_user_id() -> None:
    assert session_user_id(create_session_token("someone")) == "someone"


def test_legacy_token_without_user_id_resolves_to_default() -> None:
    """Tokens issued before per-user sessions must not sign people out."""
    from short_timer.auth import _serializer

    legacy = _serializer().dumps({"authenticated": True})
    assert session_user_id(legacy) == DEFAULT_OWNER_ID


def test_unsigned_token_has_no_user() -> None:
    assert session_user_id("not-a-real-token") is None


def test_token_without_authenticated_flag_is_rejected() -> None:
    from short_timer.auth import _serializer

    forged = _serializer().dumps({"user_id": "someone-else"})
    assert session_user_id(forged) is None


# --- The seeded default user -------------------------------------------------


async def test_default_user_id_matches_backfilled_owner_id() -> None:
    """Workouts saved before accounts existed must keep belonging to someone."""
    await ensure_default_user()
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    assert user.id == DEFAULT_OWNER_ID


async def test_seeding_is_idempotent() -> None:
    assert await ensure_default_user() is True
    assert await ensure_default_user() is False
    assert await get_users_collection().count_documents({}) == 1


async def test_reseeding_does_not_clobber_existing_config(authed_client: AsyncClient) -> None:
    await authed_client.put(
        "/api/me/config", json={"wodify_member": {"whiteboard_key": "wb-key-abcd1234"}}
    )
    await ensure_default_user()
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    assert user.config.wodify_member.whiteboard_key is not None


async def test_existing_workouts_survive_the_session_change(authed_client: AsyncClient) -> None:
    """The whole point of seeding with id == "default"."""
    await get_workouts_collection().insert_one(
        {
            **Workout(name="Legacy", mode=WorkoutMode.FOR_TIME).model_dump(mode="json"),
            "_id": "legacy-1",
            "owner_id": DEFAULT_OWNER_ID,
        }
    )
    listed = await authed_client.get("/api/workouts")
    assert listed.status_code == 200
    assert any(w["name"] == "Legacy" for w in listed.json()["items"])


# --- /api/me -----------------------------------------------------------------


async def test_me_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/me")).status_code == 401


async def test_me_returns_empty_config_initially(authed_client: AsyncClient) -> None:
    response = await authed_client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == DEFAULT_OWNER_ID
    assert body["secrets_available"] is True
    assert body["config"]["wodify_owner"]["api_key"] == {"is_set": False, "masked": None}
    assert body["config"]["wodify_member"]["enabled"] is False


async def test_config_update_stores_and_masks_credential(authed_client: AsyncClient) -> None:
    response = await authed_client.put(
        "/api/me/config",
        json={
            "wodify_owner": {
                "api_key": "wodify-secret-key-9876",
                "location": "Main",
                "program": "CrossFit",
                "enabled": True,
            }
        },
    )
    assert response.status_code == 200
    owner = response.json()["config"]["wodify_owner"]
    assert owner["api_key"] == {"is_set": True, "masked": "••••9876"}
    assert owner["location"] == "Main"
    assert owner["enabled"] is True


async def test_credential_is_never_returned_in_full(authed_client: AsyncClient) -> None:
    secret = "wodify-secret-key-9876"
    await authed_client.put("/api/me/config", json={"wodify_owner": {"api_key": secret}})
    response = await authed_client.get("/api/me")
    assert secret not in response.text


async def test_credential_is_encrypted_at_rest(authed_client: AsyncClient) -> None:
    secret = "wodify-secret-key-9876"
    await authed_client.put("/api/me/config", json={"wodify_owner": {"api_key": secret}})

    raw = await get_users_collection().find_one({"_id": DEFAULT_OWNER_ID})
    assert raw is not None
    stored = raw["config"]["wodify_owner"]["api_key"]
    assert secret not in str(stored)
    # …and the server can still read it back.
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    assert user.config.wodify_owner.api_key is not None
    assert decrypt(user.config.wodify_owner.api_key) == secret


async def test_omitted_credential_is_left_alone(authed_client: AsyncClient) -> None:
    """Toggling `enabled` must not require re-entering the key."""
    await authed_client.put(
        "/api/me/config", json={"wodify_owner": {"api_key": "wodify-secret-key-9876"}}
    )
    response = await authed_client.put(
        "/api/me/config", json={"wodify_owner": {"enabled": True, "location": "Downtown"}}
    )
    owner = response.json()["config"]["wodify_owner"]
    assert owner["api_key"]["is_set"] is True
    assert owner["api_key"]["masked"] == "••••9876"
    assert owner["enabled"] is True
    assert owner["location"] == "Downtown"


async def test_empty_string_clears_credential(authed_client: AsyncClient) -> None:
    await authed_client.put(
        "/api/me/config", json={"wodify_owner": {"api_key": "wodify-secret-key-9876"}}
    )
    response = await authed_client.put("/api/me/config", json={"wodify_owner": {"api_key": ""}})
    assert response.json()["config"]["wodify_owner"]["api_key"]["is_set"] is False


async def test_untouched_section_is_preserved(authed_client: AsyncClient) -> None:
    await authed_client.put(
        "/api/me/config", json={"wodify_member": {"whiteboard_key": "wb-key-abcd1234"}}
    )
    response = await authed_client.put(
        "/api/me/config", json={"wodify_owner": {"location": "Main"}}
    )
    config = response.json()["config"]
    assert config["wodify_member"]["whiteboard_key"]["is_set"] is True
    assert config["wodify_owner"]["location"] == "Main"


async def test_config_is_scoped_to_the_session_user(authed_client: AsyncClient) -> None:
    """Another user's session must not see this user's credentials."""
    await authed_client.put(
        "/api/me/config", json={"wodify_owner": {"api_key": "wodify-secret-key-9876"}}
    )
    from short_timer.auth import SESSION_COOKIE_NAME
    from short_timer.models import User

    other = User(id="someone-else", display_name="Other")
    doc = other.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    await get_users_collection().insert_one(doc)

    authed_client.cookies.set(SESSION_COOKIE_NAME, create_session_token("someone-else"))
    response = await authed_client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "someone-else"
    assert body["config"]["wodify_owner"]["api_key"]["is_set"] is False


async def test_saving_credential_without_keys_reports_unavailable(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECRETS_KEYS", "")
    get_settings.cache_clear()
    crypto._cipher.cache_clear()

    response = await authed_client.put(
        "/api/me/config", json={"wodify_owner": {"api_key": "wodify-secret-key-9876"}}
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
        "/api/me/config", json={"wodify_owner": {"location": "Main", "enabled": True}}
    )
    assert response.status_code == 200
    assert response.json()["config"]["wodify_owner"]["location"] == "Main"
