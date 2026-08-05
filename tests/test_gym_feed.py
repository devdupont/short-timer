"""The gym feed end to end: config resolution, caching, and the API."""

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from short_timer import crypto
from short_timer.app import app
from short_timer.auth import DEFAULT_OWNER_ID, SESSION_COOKIE_NAME, create_session_token
from short_timer.config import get_settings
from short_timer.crypto import encrypt, generate_key
from short_timer.db import (
    get_gym_cache_collection,
    get_users_collection,
    get_workouts_collection,
)
from short_timer.dedup import source_hash
from short_timer.gym_cache import (
    gym_fingerprint,
    read_cached,
    refresh_all_configured,
    resolve_source,
)
from short_timer.models import GymProvider, User, Workout, WorkoutMode
from short_timer.users import ensure_default_user, get_user

WHITEBOARD = "https://app.wodify.com/Performance/PublicWhiteboard.aspx"
PROGRAM_API = "https://api.wodify.com/v1/workouts/formattedworkout"

BOARD_HTML = """
<html><body><nav>Home</nav><div>
<p>For Time:</p><p>21-15-9</p><p>Thrusters 95/65 lb</p><p>Pull-ups</p>
</div></body></html>
"""


@pytest.fixture(autouse=True)
def _secrets_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_KEYS", generate_key())
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._cipher.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-parse would otherwise call Anthropic for every cached day."""
    from short_timer import gym_cache
    from short_timer.models import Workout, WorkoutMode

    async def fake_parse(text: str, name_hint: str | None = None) -> Workout:
        return Workout(name=name_hint or "Parsed", mode=WorkoutMode.FOR_TIME, source_text=text)

    monkeypatch.setattr(gym_cache, "parse_workout_text", fake_parse)


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


async def _configure_member(user_id: str = DEFAULT_OWNER_ID, key: str = "wb-key-1234") -> None:
    await ensure_default_user()
    await get_users_collection().update_one(
        {"_id": user_id},
        {
            "$set": {
                "config.wodify_member.whiteboard_key": encrypt(key).model_dump(mode="json"),
                "config.wodify_member.enabled": True,
            }
        },
        upsert=True,
    )


# --- Resolving configuration -------------------------------------------------


async def test_unconfigured_user_resolves_to_nothing() -> None:
    await ensure_default_user()
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    assert resolve_source(user) is None


async def test_disabled_config_resolves_to_nothing() -> None:
    """A saved key that's switched off must not be fetched."""
    await _configure_member()
    await get_users_collection().update_one(
        {"_id": DEFAULT_OWNER_ID}, {"$set": {"config.wodify_member.enabled": False}}
    )
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    assert resolve_source(user) is None


async def test_member_route_wins_when_both_configured() -> None:
    await _configure_member()
    await get_users_collection().update_one(
        {"_id": DEFAULT_OWNER_ID},
        {
            "$set": {
                "config.wodify_owner.api_key": encrypt("api-key-9999").model_dump(mode="json"),
                "config.wodify_owner.location": "Main",
                "config.wodify_owner.program": "CrossFit",
                "config.wodify_owner.enabled": True,
            }
        },
    )
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    source = resolve_source(user)
    assert source is not None
    assert source.provider == GymProvider.WODIFY_MEMBER


async def test_credential_that_cannot_be_decrypted_resolves_to_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rotated-away key must degrade to "unconfigured", not crash the feed."""
    await _configure_member()
    monkeypatch.setenv("SECRETS_KEYS", generate_key())
    get_settings.cache_clear()
    crypto._cipher.cache_clear()

    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    assert resolve_source(user) is None


# --- The API -----------------------------------------------------------------


async def test_gym_feed_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/gym/wods")).status_code == 401


async def test_unconfigured_feed_is_empty_not_an_error(authed_client: AsyncClient) -> None:
    response = await authed_client.get("/api/gym/wods")
    assert response.status_code == 200
    assert response.json() == {"configured": False, "wods": []}


@respx.mock
async def test_configured_feed_returns_workouts(authed_client: AsyncClient) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member()

    response = await authed_client.get("/api/gym/wods?days=3")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert len(body["wods"]) == 3
    assert "Thrusters 95/65 lb" in body["wods"][0]["text"]
    assert body["wods"][0]["saved_workout_id"] is None


@respx.mock
async def test_feed_marks_workouts_already_in_the_library(authed_client: AsyncClient) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member()

    first = await authed_client.get("/api/gym/wods?days=1")
    text = first.json()["wods"][0]["text"]

    # Save it the way the UI would, then confirm the feed cross-references it.
    workout = Workout(name="Saved", mode=WorkoutMode.FOR_TIME, source_text=text)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = DEFAULT_OWNER_ID
    doc["source_hash"] = source_hash(text)
    await get_workouts_collection().insert_one(doc)

    again = await authed_client.get("/api/gym/wods?days=1")
    assert again.json()["wods"][0]["saved_workout_id"] == workout.id


@respx.mock
async def test_feed_is_served_from_cache_on_repeat_requests(
    authed_client: AsyncClient,
) -> None:
    """No request should wait on Wodify once the cache is warm."""
    route = respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member()

    await authed_client.get("/api/gym/wods?days=2")
    calls_after_first = route.call_count
    assert calls_after_first > 0

    await authed_client.get("/api/gym/wods?days=2")
    assert route.call_count == calls_after_first


# --- Tenancy -----------------------------------------------------------------


@respx.mock
async def test_one_gyms_workouts_never_reach_another_gym(authed_client: AsyncClient) -> None:
    """The cache is shared, so this is the isolation that matters."""
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(key="gym-a-key")
    await authed_client.get("/api/gym/wods?days=2")

    # A second user at a different gym.
    other = User(id="other-user")
    doc = other.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    await get_users_collection().insert_one(doc)
    await _configure_member(user_id="other-user", key="gym-b-key")

    a = gym_fingerprint("gym-a-key", GymProvider.WODIFY_MEMBER)
    b = gym_fingerprint("gym-b-key", GymProvider.WODIFY_MEMBER)
    assert a != b
    # A cold read fills the whole window, not just the requested days.
    assert len(await read_cached(a, 5)) == 5
    # Gym B has its own, empty, cache namespace until it's fetched.
    assert await read_cached(b, 5) == []


@respx.mock
async def test_members_of_the_same_gym_share_one_fetch() -> None:
    """A gym with many members should be fetched once, not once per member."""
    route = respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(key="shared-gym-key")
    for user_id in ("member-2", "member-3"):
        doc = User(id=user_id).model_dump(mode="json")
        doc["_id"] = doc.pop("id")
        await get_users_collection().insert_one(doc)
        await _configure_member(user_id=user_id, key="shared-gym-key")

    refreshed = await refresh_all_configured()
    assert refreshed == 1
    # 14 cached days for one gym, not 42 across three identical members.
    assert route.call_count == 14


@respx.mock
async def test_stored_cache_holds_no_credential() -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(key="very-secret-key")
    await refresh_all_configured()

    async for doc in get_gym_cache_collection().find({}):
        assert "very-secret-key" not in str(doc)


@respx.mock
async def test_refresh_skips_users_without_a_gym() -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await ensure_default_user()
    assert await refresh_all_configured() == 0


async def test_feed_is_scoped_to_the_session_user(authed_client: AsyncClient) -> None:
    """Another user's session must not inherit this user's gym."""
    await _configure_member()
    other = User(id="stranger")
    doc = other.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    await get_users_collection().insert_one(doc)

    authed_client.cookies.set(SESSION_COOKIE_NAME, create_session_token("stranger"))
    response = await authed_client.get("/api/gym/wods")
    assert response.json() == {"configured": False, "wods": []}


# --- Migrating pre-provider config -------------------------------------------


async def test_legacy_wodify_config_becomes_a_provider_connection() -> None:
    """Documents written before providers existed must keep working.

    `_configure_member` writes the old `config.wodify_member.*` shape straight
    into Mongo, which is exactly what a real pre-migration document looks like.
    """
    await _configure_member(key="legacy-key-1234")
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None

    connection = user.config.connection(GymProvider.WODIFY_MEMBER)
    assert connection is not None
    assert connection.enabled is True
    assert crypto.decrypt(connection.credential) == "legacy-key-1234"
    # And the feed still resolves off it.
    source = resolve_source(user)
    assert source is not None and source.provider is GymProvider.WODIFY_MEMBER


async def test_migration_does_not_overwrite_an_edited_connection() -> None:
    """A user who has since re-saved shouldn't get the stale copy back."""
    await _configure_member(key="old-key-1234")
    await get_users_collection().update_one(
        {"_id": DEFAULT_OWNER_ID},
        {
            "$set": {
                "config.gyms": [
                    {
                        "provider": GymProvider.WODIFY_MEMBER.value,
                        "credential": encrypt("new-key-5678").model_dump(mode="json"),
                        "enabled": True,
                    }
                ]
            }
        },
    )
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None
    connection = user.config.connection(GymProvider.WODIFY_MEMBER)
    assert connection is not None
    assert crypto.decrypt(connection.credential) == "new-key-5678"


async def test_the_startup_sweep_persists_the_migration() -> None:
    """Reads migrate every time; the sweep is what makes it stick."""
    from short_timer.db import backfill_gym_connections

    await _configure_member(key="legacy-key-1234")
    assert await backfill_gym_connections() == 1

    raw = await get_users_collection().find_one({"_id": DEFAULT_OWNER_ID})
    assert raw is not None
    assert len(raw["config"]["gyms"]) == 1
    assert raw["config"]["wodify_member"]["whiteboard_key"] is None
    # Idempotent: nothing left to migrate on a second pass.
    assert await backfill_gym_connections() == 0


async def test_a_user_who_never_connected_a_gym_is_not_touched() -> None:
    from short_timer.db import backfill_gym_connections

    await ensure_default_user()
    assert await backfill_gym_connections() == 0
    user = await get_user(DEFAULT_OWNER_ID)
    assert user is not None and user.config.gyms == []


# --- The source registry over the wire ---------------------------------------


async def test_providers_endpoint_describes_every_platform(authed_client: AsyncClient) -> None:
    """The settings screen renders from this, so it has to be complete."""
    response = await authed_client.get("/api/gym/providers")
    assert response.status_code == 200
    providers = response.json()
    assert {p["provider"] for p in providers} == {p.value for p in GymProvider}
    for provider in providers:
        assert provider["platform"] and provider["label"]
        assert provider["credential_label"] and provider["credential_hint"]


async def test_providers_endpoint_requires_a_session(client: AsyncClient) -> None:
    assert (await client.get("/api/gym/providers")).status_code == 401


async def test_health_reports_a_connection_that_has_never_fetched(
    authed_client: AsyncClient,
) -> None:
    """The one signal that distinguishes a bad key from a quiet gym."""
    await _configure_member(key="never-fetched-key")
    response = await authed_client.get("/api/gym/health")
    assert response.status_code == 200
    [entry] = response.json()
    assert entry["provider"] == GymProvider.WODIFY_MEMBER.value
    assert entry["last_fetched_at"] is None
    assert entry["cached_days"] == 0


@respx.mock
async def test_health_reports_a_working_connection(authed_client: AsyncClient) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member()
    await authed_client.get("/api/gym/wods?days=2")

    [entry] = (await authed_client.get("/api/gym/health")).json()
    assert entry["last_fetched_at"] is not None
    assert entry["cached_days"] > 0


async def test_health_is_empty_when_nothing_is_connected(authed_client: AsyncClient) -> None:
    assert (await authed_client.get("/api/gym/health")).json() == []


# --- SugarWOD through the same machinery -------------------------------------


@respx.mock
async def test_a_sugarwod_gym_feeds_the_home_page(authed_client: AsyncClient) -> None:
    """The point of the registry: a new platform needs no new feed plumbing."""
    respx.get("https://api.sugarwod.com/v2/workouts").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "workouts",
                        "id": "w1",
                        "attributes": {
                            "title": "Fran",
                            "description": "21-15-9 Thrusters 95 lb and Pull-ups for time",
                            "date_int": 20260804,
                        },
                    }
                ]
            },
        )
    )
    await ensure_default_user()
    await get_users_collection().update_one(
        {"_id": DEFAULT_OWNER_ID},
        {
            "$set": {
                "config.gyms": [
                    {
                        "provider": GymProvider.SUGARWOD_OWNER.value,
                        "credential": encrypt("sugar-key-1234").model_dump(mode="json"),
                        "enabled": True,
                    }
                ]
            }
        },
    )

    response = await authed_client.get("/api/gym/wods?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["wods"][0]["title"] == "Fran"
    assert body["wods"][0]["provider"] == GymProvider.SUGARWOD_OWNER.value


async def test_two_platforms_do_not_share_a_cache_namespace() -> None:
    """Same credential string, different platform — must not collide."""
    from short_timer.gym_cache import gym_fingerprint

    assert gym_fingerprint("same-key", GymProvider.WODIFY_OWNER) != gym_fingerprint(
        "same-key", GymProvider.SUGARWOD_OWNER
    )


# --- Wire contract with the frontend -----------------------------------------
# The settings screen is generated from these payloads, and TypeScript can only
# check what it was told the shape is. Pinning the key sets here means a field
# added or renamed on the server shows up as a failing test rather than as
# `undefined` in a browser.


async def test_provider_payload_matches_the_frontend_interface(
    authed_client: AsyncClient,
) -> None:
    """Keys must match `GymProviderInfo` in web/src/types.ts."""
    [provider, *_] = (await authed_client.get("/api/gym/providers")).json()
    assert set(provider) == {
        "provider",
        "platform",
        "label",
        "blurb",
        "link_label",
        "credential_label",
        "credential_hint",
        "help_text",
        "location",
        "program",
    }
    # A declared field carries everything `GymFieldInfo` needs to render it.
    assert set(provider["location"]) == {"label", "placeholder", "required"}


async def test_health_payload_matches_the_frontend_interface(
    authed_client: AsyncClient,
) -> None:
    """Keys must match `GymConnectionHealth` in web/src/types.ts."""
    await _configure_member()
    [entry] = (await authed_client.get("/api/gym/health")).json()
    assert set(entry) == {"provider", "last_fetched_at", "cached_days"}


async def test_config_payload_matches_the_frontend_interface(
    authed_client: AsyncClient,
) -> None:
    """Keys must match `UserConfig` / `GymConnection` in web/src/types.ts."""
    await _configure_member()
    config = (await authed_client.get("/api/me")).json()["config"]
    # The two Wodify keys are the deprecated mirrors; the frontend reads `gyms`.
    assert {"gyms", "feeds"} <= set(config)
    [connection] = config["gyms"]
    assert set(connection) == {"provider", "credential", "location", "program", "enabled"}
    assert set(connection["credential"]) == {"is_set", "masked"}


@respx.mock
async def test_gym_entry_payload_matches_the_frontend_interface(
    authed_client: AsyncClient,
) -> None:
    """Keys must match `GymWodEntry` in web/src/types.ts."""
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member()
    [entry, *_] = (await authed_client.get("/api/gym/wods?days=2")).json()["wods"]
    assert set(entry) == {
        "date",
        "title",
        "text",
        "url",
        "provider",
        "saved_workout_id",
        "link_label",
    }
    # The card names the platform the workout actually came from.
    assert entry["link_label"] == "View on Wodify ↗"
