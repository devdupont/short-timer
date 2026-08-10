"""The MCP server acts on one owner's library, not the whole collection."""

from collections.abc import AsyncGenerator

import pytest

from shortimer.auth import api_tokens
from shortimer.cache.db import get_workouts_collection
from shortimer.config import get_settings
from shortimer.mcp_server import create_timer_workout, get_workout, search_workouts
from shortimer.model.token import ApiTokenScope
from shortimer.model.user import User
from shortimer.model.workout import Workout, WorkoutMode


@pytest.fixture(autouse=True)
async def mcp_owner(account: User, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[str]:
    """Point the server at an account by issuing it a token.

    The owner comes from the token now, not from configuration, so becoming
    somebody is minting a credential for them rather than naming them.
    """
    raw, _ = await api_tokens.create_token(
        user_id=account.id,
        name="test",
        scopes=[ApiTokenScope.LIBRARY_READ, ApiTokenScope.LIBRARY_WRITE],
    )
    monkeypatch.setenv("MCP_API_TOKEN", raw)
    get_settings.cache_clear()
    yield account.id
    get_settings.cache_clear()


async def _insert(workout_id: str, name: str, owner_id: str, **fields: object) -> None:
    """Insert a bare workout document directly, bypassing the MCP write path."""
    await get_workouts_collection().insert_one(
        {
            **Workout(name=name, mode=WorkoutMode.FOR_TIME, **fields).model_dump(mode="json"),
            "_id": workout_id,
            "owner_id": owner_id,
        }
    )


async def test_search_only_sees_its_own_owners_workouts(mcp_owner: str) -> None:
    """A name shared by two owners' workouts: search and the empty-query listing return only mine."""
    await _insert("mine", "Fran", mcp_owner)
    await _insert("theirs", "Fran", "another-user")

    results = await search_workouts(query="Fran")
    assert [doc["id"] for doc in results] == ["mine"]

    # An empty query lists the library rather than the whole collection.
    assert [doc["id"] for doc in await search_workouts()] == ["mine"]


async def test_search_by_category_is_owner_scoped_too(mcp_owner: str) -> None:
    """A category filter shared by two owners still returns only mine."""
    await _insert("mine", "Cindy", mcp_owner, category="girls")
    await _insert("theirs", "Not Mine", "another-user", category="girls")

    assert [doc["id"] for doc in await search_workouts(category="girls")] == ["mine"]


async def test_search_treats_the_query_literally(mcp_owner: str) -> None:
    """Regex metacharacters in the query match themselves rather than being interpreted."""
    await _insert("mine", "5+ rounds", mcp_owner)

    assert [doc["id"] for doc in await search_workouts(query="5+")] == ["mine"]
    assert await search_workouts(query=".*") == []


async def test_get_workout_refuses_another_owners_id() -> None:
    """Fetching a real workout id that belongs to a different owner returns None, not the row."""
    await _insert("theirs", "Not Mine", "another-user")

    assert await get_workout("theirs") is None


async def test_created_workouts_are_owned(mcp_owner: str) -> None:
    """An unowned write would be invisible to the web app's library."""
    created = await create_timer_workout(
        name="Ladder",
        mode="interval",
        segments=[{"work_seconds": 300, "movements": [{"name": "Row"}]}],
    )

    doc = await get_workouts_collection().find_one({"_id": created["id"]})
    assert doc is not None
    assert doc["owner_id"] == mcp_owner

    # ...and it comes back through the server's own read path.
    assert (await get_workout(created["id"]))["name"] == "Ladder"  # type: ignore[index]


async def test_created_workout_can_count_its_sets_up() -> None:
    """Authoring a set-time workout through MCP has to reach the same clock."""
    created = await create_timer_workout(
        name="Every 3:00 x 5 Sets",
        mode="emom",
        rounds=5,
        work_seconds=180,
        interval_clock="count_up",
        segments=[{"movements": [{"name": "Rope Climb", "reps": 3}]}],
    )

    assert created["interval_clock"] == "count_up"
    saved = await get_workout(created["id"])
    assert saved is not None
    assert saved["interval_clock"] == "count_up"


async def test_created_workouts_count_down_by_default() -> None:
    """Omitting `interval_clock` yields the count-down default."""
    created = await create_timer_workout(name="Chelsea", mode="emom", segments=[])

    assert created["interval_clock"] == "count_down"
