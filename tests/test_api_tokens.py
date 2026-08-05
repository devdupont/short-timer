"""Per-user API tokens, and the MCP server that authenticates with one."""

import pytest
from conftest import TEST_PASSWORD
from httpx import AsyncClient

from short_timer import api_tokens, mcp_server
from short_timer.config import get_settings
from short_timer.db import get_api_tokens_collection
from short_timer.models import ApiTokenScope, User

READ = [ApiTokenScope.LIBRARY_READ]
BOTH = [ApiTokenScope.LIBRARY_READ, ApiTokenScope.LIBRARY_WRITE]


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Minting -----------------------------------------------------------------


async def test_creating_a_token_returns_it_exactly_once(authed_client: AsyncClient) -> None:
    response = await authed_client.post(
        "/api/me/tokens",
        json={"name": "MCP", "scopes": ["library:read"], "current_password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token"].startswith("st_")
    assert body["api_token"]["name"] == "MCP"

    # The listing never carries the value again — only the prefix.
    listed = (await authed_client.get("/api/me/tokens")).json()
    assert body["token"] not in str(listed)
    assert listed[0]["prefix"] == body["token"][: len(listed[0]["prefix"])]


async def test_minting_requires_the_current_password(authed_client: AsyncClient) -> None:
    """It creates a credential that outlives the session that made it."""
    response = await authed_client.post(
        "/api/me/tokens",
        json={"name": "MCP", "scopes": ["library:read"], "current_password": "wrong"},
    )
    assert response.status_code == 403
    assert await get_api_tokens_collection().count_documents({}) == 0


async def test_a_token_needs_at_least_one_scope(authed_client: AsyncClient) -> None:
    response = await authed_client.post(
        "/api/me/tokens",
        json={"name": "MCP", "scopes": [], "current_password": TEST_PASSWORD},
    )
    assert response.status_code == 422


async def test_tokens_require_a_session(client: AsyncClient) -> None:
    assert (await client.get("/api/me/tokens")).status_code == 401


async def test_the_stored_form_is_not_the_token(account: User) -> None:
    raw, token = await api_tokens.create_token(user_id=account.id, name="MCP", scopes=READ)
    docs = [d async for d in get_api_tokens_collection().find({})]
    assert raw not in str(docs)
    assert docs[0]["_id"] == token.id


# --- Revoking -----------------------------------------------------------------


async def test_revoking_a_token_stops_it_working(authed_client: AsyncClient, account: User) -> None:
    raw, token = await api_tokens.create_token(user_id=account.id, name="MCP", scopes=READ)
    assert await api_tokens.resolve_token(raw) is not None

    assert (await authed_client.delete(f"/api/me/tokens/{token.id}")).status_code == 204
    assert await api_tokens.resolve_token(raw) is None


async def test_you_cannot_revoke_someone_elses_token(
    authed_client: AsyncClient, admin_account: User
) -> None:
    raw, token = await api_tokens.create_token(user_id=admin_account.id, name="Theirs", scopes=READ)

    assert (await authed_client.delete(f"/api/me/tokens/{token.id}")).status_code == 404
    # …and it still works, because nothing was deleted.
    assert await api_tokens.resolve_token(raw) is not None


async def test_the_listing_is_scoped_to_the_caller(
    authed_client: AsyncClient, account: User, admin_account: User
) -> None:
    await api_tokens.create_token(user_id=account.id, name="Mine", scopes=READ)
    await api_tokens.create_token(user_id=admin_account.id, name="Theirs", scopes=READ)

    listed = (await authed_client.get("/api/me/tokens")).json()
    assert [row["name"] for row in listed] == ["Mine"]


async def test_use_is_recorded(account: User) -> None:
    """ "Last used" is how you decide which of four tokens is safe to revoke."""
    raw, token = await api_tokens.create_token(user_id=account.id, name="MCP", scopes=READ)
    assert token.last_used_at is None

    await api_tokens.resolve_token(raw)
    doc = await get_api_tokens_collection().find_one({"_id": token.id})
    assert doc is not None and doc["last_used_at"] is not None


# --- The MCP server -----------------------------------------------------------


async def test_mcp_refuses_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_API_TOKEN", "")
    get_settings.cache_clear()
    with pytest.raises(mcp_server.NotAuthorized, match="not set"):
        await mcp_server.search_workouts()


async def test_mcp_refuses_an_unknown_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_API_TOKEN", "st_made-up")
    get_settings.cache_clear()
    with pytest.raises(mcp_server.NotAuthorized, match="not valid"):
        await mcp_server.search_workouts()


async def test_mcp_refuses_a_revoked_token(account: User, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revocation takes effect on the next call, not the next restart."""
    raw, token = await api_tokens.create_token(user_id=account.id, name="MCP", scopes=BOTH)
    monkeypatch.setenv("MCP_API_TOKEN", raw)
    get_settings.cache_clear()

    assert await mcp_server.search_workouts() == []

    await api_tokens.revoke_token(account.id, token.id)
    with pytest.raises(mcp_server.NotAuthorized):
        await mcp_server.search_workouts()


async def test_a_read_only_token_cannot_write(
    account: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, _ = await api_tokens.create_token(user_id=account.id, name="MCP", scopes=READ)
    monkeypatch.setenv("MCP_API_TOKEN", raw)
    get_settings.cache_clear()

    # Reading is fine.
    assert await mcp_server.search_workouts() == []

    with pytest.raises(mcp_server.NotAuthorized, match="library:write"):
        await mcp_server.create_timer_workout(name="Fran", mode="for_time", segments=[])


async def test_a_written_workout_belongs_to_the_tokens_owner(
    account: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: the token decides whose library this is."""
    raw, _ = await api_tokens.create_token(user_id=account.id, name="MCP", scopes=BOTH)
    monkeypatch.setenv("MCP_API_TOKEN", raw)
    get_settings.cache_clear()

    created = await mcp_server.create_timer_workout(name="Fran", mode="for_time", segments=[])

    from short_timer.db import get_workouts_collection

    doc = await get_workouts_collection().find_one({"_id": created["id"]})
    assert doc is not None and doc["owner_id"] == account.id


async def test_reads_never_cross_owners(
    account: User, admin_account: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from short_timer.db import get_workouts_collection
    from short_timer.models import Workout, WorkoutMode

    for owner, workout_id in ((account.id, "mine"), (admin_account.id, "theirs")):
        await get_workouts_collection().insert_one(
            {
                **Workout(name="Fran", mode=WorkoutMode.FOR_TIME).model_dump(mode="json"),
                "_id": workout_id,
                "owner_id": owner,
            }
        )

    raw, _ = await api_tokens.create_token(user_id=account.id, name="MCP", scopes=READ)
    monkeypatch.setenv("MCP_API_TOKEN", raw)
    get_settings.cache_clear()

    assert [doc["id"] for doc in await mcp_server.search_workouts(query="Fran")] == ["mine"]
    assert await mcp_server.get_workout("theirs") is None
    assert (await mcp_server.get_workout("mine")) is not None
