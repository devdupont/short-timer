"""The gym feed end to end: config resolution, caching, and the API."""

from collections.abc import Awaitable, Callable, Generator

import httpx
import pytest
import respx
from httpx import AsyncClient

from shortimer.cache import crypto
from shortimer.cache.crypto import encrypt, generate_key
from shortimer.cache.db import (
    get_gym_cache_collection,
    get_users_collection,
    get_workouts_collection,
)
from shortimer.cache.gym import (
    gym_fingerprint,
    read_cached,
    refresh_all_configured,
    resolve_source,
)
from shortimer.config import get_settings
from shortimer.model.gym import GymProvider
from shortimer.model.user import User
from shortimer.model.workout import Workout, WorkoutMode
from shortimer.users import get_user
from shortimer.util.dedup import source_hash

WHITEBOARD = "https://app.wodify.com/Performance/PublicWhiteboard.aspx"
PROGRAM_API = "https://api.wodify.com/v1/workouts/formattedworkout"

BOARD_HTML = """
<html><body><nav>Home</nav><div>
<p>For Time:</p><p>21-15-9</p><p>Thrusters 95/65 lb</p><p>Pull-ups</p>
</div></body></html>
"""


@pytest.fixture(autouse=True)
def _secrets_configured(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("SECRETS_KEYS", generate_key())
    get_settings.cache_clear()
    crypto._cipher.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._cipher.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-parse would otherwise call Anthropic for every cached day."""
    from shortimer.cache import gym
    from shortimer.model.workout import Workout, WorkoutMode

    async def fake_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        return Workout(name=name_hint or "Parsed", mode=WorkoutMode.FOR_TIME, source_text=text)

    monkeypatch.setattr(gym, "parse_workout_text", fake_parse)


async def _configure(
    provider: GymProvider,
    user_id: str,
    key: str = "wb-key-1234",
    **fields: object,
) -> None:
    """Store one gym connection, bypassing the API so tests can set any shape."""
    connection = {
        "provider": provider.value,
        "credential": encrypt(key).model_dump(mode="json"),
        "enabled": True,
        **fields,
    }
    await get_users_collection().update_one(
        {"_id": user_id}, {"$set": {"config.gyms": [connection]}}, upsert=True
    )


async def _configure_member(user_id: str, key: str = "wb-key-1234") -> None:
    await _configure(GymProvider.WODIFY_MEMBER, user_id=user_id, key=key)


# --- Resolving configuration -------------------------------------------------


async def test_unconfigured_user_resolves_to_nothing(account: User) -> None:
    user = await get_user(account.id)
    assert user is not None
    assert resolve_source(user) is None


async def test_disabled_config_resolves_to_nothing(account: User) -> None:
    """A saved key that's switched off must not be fetched."""
    await _configure_member(account.id)
    await get_users_collection().update_one(
        {"_id": account.id}, {"$set": {"config.gyms.0.enabled": False}}
    )
    user = await get_user(account.id)
    assert user is not None
    assert resolve_source(user) is None


async def test_member_route_wins_when_both_configured(account: User) -> None:
    """PROVIDER_PRIORITY decides, not which connection was stored first."""
    await get_users_collection().update_one(
        {"_id": account.id},
        {
            "$set": {
                "config.gyms": [
                    # Owner first in storage order, to prove order doesn't decide.
                    {
                        "provider": GymProvider.WODIFY_OWNER.value,
                        "credential": encrypt("api-key-9999").model_dump(mode="json"),
                        "location": "Main",
                        "program": "CrossFit",
                        "enabled": True,
                    },
                    {
                        "provider": GymProvider.WODIFY_MEMBER.value,
                        "credential": encrypt("wb-key-1234").model_dump(mode="json"),
                        "enabled": True,
                    },
                ]
            }
        },
    )
    user = await get_user(account.id)
    assert user is not None
    source = resolve_source(user)
    assert source is not None
    assert source.provider == GymProvider.WODIFY_MEMBER


async def test_credential_that_cannot_be_decrypted_resolves_to_nothing(
    monkeypatch: pytest.MonkeyPatch, account: User
) -> None:
    """A rotated-away key must degrade to "unconfigured", not crash the feed."""
    await _configure_member(account.id)
    monkeypatch.setenv("SECRETS_KEYS", generate_key())
    get_settings.cache_clear()
    crypto._cipher.cache_clear()

    user = await get_user(account.id)
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
async def test_configured_feed_returns_workouts(authed_client: AsyncClient, account: User) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id)

    response = await authed_client.get("/api/gym/wods?days=3")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert len(body["wods"]) == 3
    assert "Thrusters 95/65 lb" in body["wods"][0]["text"]
    assert body["wods"][0]["saved_workout_id"] is None


@respx.mock
async def test_feed_marks_workouts_already_in_the_library(
    authed_client: AsyncClient, account: User
) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id)

    first = await authed_client.get("/api/gym/wods?days=1")
    text = first.json()["wods"][0]["text"]

    # Save it the way the UI would, then confirm the feed cross-references it.
    workout = Workout(name="Saved", mode=WorkoutMode.FOR_TIME, source_text=text)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = account.id
    doc["source_hash"] = source_hash(text)
    await get_workouts_collection().insert_one(doc)

    again = await authed_client.get("/api/gym/wods?days=1")
    assert again.json()["wods"][0]["saved_workout_id"] == workout.id


@respx.mock
async def test_feed_is_served_from_cache_on_repeat_requests(
    authed_client: AsyncClient, account: User
) -> None:
    """No request should wait on Wodify once the cache is warm."""
    route = respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id)

    await authed_client.get("/api/gym/wods?days=2")
    calls_after_first = route.call_count
    assert calls_after_first > 0

    await authed_client.get("/api/gym/wods?days=2")
    assert route.call_count == calls_after_first


# --- Tenancy -----------------------------------------------------------------


@respx.mock
async def test_one_gyms_workouts_never_reach_another_gym(
    authed_client: AsyncClient, account: User
) -> None:
    """The cache is shared, so this is the isolation that matters."""
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id, key="gym-a-key")
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
async def test_members_of_the_same_gym_share_one_fetch(account: User) -> None:
    """A gym with many members should be fetched once, not once per member."""
    route = respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id, key="shared-gym-key")
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
async def test_stored_cache_holds_no_credential(account: User) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id, key="very-secret-key")
    await refresh_all_configured()

    async for doc in get_gym_cache_collection().find({}):
        assert "very-secret-key" not in str(doc)


@respx.mock
async def test_refresh_skips_users_without_a_gym() -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    assert await refresh_all_configured() == 0


async def test_feed_is_scoped_to_the_session_user(
    authed_client: AsyncClient,
    sign_in_as: Callable[[AsyncClient, str], Awaitable[str]],
    account: User,
) -> None:
    """Another user's session must not inherit this user's gym."""
    await _configure_member(account.id)
    other = User(id="stranger")
    doc = other.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    await get_users_collection().insert_one(doc)

    await sign_in_as(authed_client, "stranger")
    response = await authed_client.get("/api/gym/wods")
    assert response.json() == {"configured": False, "wods": []}


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
    authed_client: AsyncClient, account: User
) -> None:
    """The one signal that distinguishes a bad key from a quiet gym."""
    await _configure_member(account.id, key="never-fetched-key")
    response = await authed_client.get("/api/gym/health")
    assert response.status_code == 200
    [entry] = response.json()
    assert entry["provider"] == GymProvider.WODIFY_MEMBER.value
    assert entry["last_fetched_at"] is None
    assert entry["cached_days"] == 0


@respx.mock
async def test_health_reports_a_working_connection(
    authed_client: AsyncClient, account: User
) -> None:
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id)
    await authed_client.get("/api/gym/wods?days=2")

    [entry] = (await authed_client.get("/api/gym/health")).json()
    assert entry["last_fetched_at"] is not None
    assert entry["cached_days"] > 0


async def test_health_is_empty_when_nothing_is_connected(authed_client: AsyncClient) -> None:
    assert (await authed_client.get("/api/gym/health")).json() == []


# --- SugarWOD through the same machinery -------------------------------------


@respx.mock
async def test_a_sugarwod_gym_feeds_the_home_page(
    authed_client: AsyncClient, account: User
) -> None:
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
    await _configure(GymProvider.SUGARWOD_OWNER, user_id=account.id, key="sugar-key-1234")

    response = await authed_client.get("/api/gym/wods?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["wods"][0]["title"] == "Fran"
    assert body["wods"][0]["provider"] == GymProvider.SUGARWOD_OWNER.value


async def test_two_platforms_do_not_share_a_cache_namespace() -> None:
    """Same credential string, different platform — must not collide."""
    from shortimer.cache.gym import gym_fingerprint

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
    authed_client: AsyncClient, account: User
) -> None:
    """Keys must match `GymConnectionHealth` in web/src/types.ts."""
    await _configure_member(account.id)
    [entry] = (await authed_client.get("/api/gym/health")).json()
    assert set(entry) == {"provider", "last_fetched_at", "cached_days"}


async def test_config_payload_matches_the_frontend_interface(
    authed_client: AsyncClient, account: User
) -> None:
    """Keys must match `UserConfig` / `GymConnection` in web/src/types.ts."""
    await _configure_member(account.id)
    config = (await authed_client.get("/api/me")).json()["config"]
    assert set(config) == {"gyms", "feeds"}
    [connection] = config["gyms"]
    assert set(connection) == {"provider", "credential", "location", "program", "enabled"}
    assert set(connection["credential"]) == {"is_set", "masked"}


@respx.mock
async def test_gym_entry_payload_matches_the_frontend_interface(
    authed_client: AsyncClient, account: User
) -> None:
    """Keys must match `GymWodEntry` in web/src/types.ts."""
    respx.get(WHITEBOARD).mock(return_value=httpx.Response(200, html=BOARD_HTML))
    await _configure_member(account.id)
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
