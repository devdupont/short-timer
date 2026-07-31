"""The MCP server acts on one owner's library, not the whole collection."""

import pytest

from short_timer.auth import DEFAULT_OWNER_ID
from short_timer.config import get_settings
from short_timer.db import get_workouts_collection
from short_timer.mcp_server import create_timer_workout, get_workout, search_workouts
from short_timer.models import Workout, WorkoutMode


@pytest.fixture(autouse=True)
def _fresh_settings() -> None:
    """Drop cached settings so a test can change MCP_OWNER_ID."""
    get_settings.cache_clear()


async def _insert(workout_id: str, name: str, owner_id: str, **fields: object) -> None:
    await get_workouts_collection().insert_one(
        {
            **Workout(name=name, mode=WorkoutMode.FOR_TIME, **fields).model_dump(mode="json"),
            "_id": workout_id,
            "owner_id": owner_id,
        }
    )


async def test_search_only_sees_its_own_owners_workouts() -> None:
    await _insert("mine", "Fran", DEFAULT_OWNER_ID)
    await _insert("theirs", "Fran", "another-user")

    results = await search_workouts(query="Fran")
    assert [doc["id"] for doc in results] == ["mine"]

    # An empty query lists the library rather than the whole collection.
    assert [doc["id"] for doc in await search_workouts()] == ["mine"]


async def test_search_by_category_is_owner_scoped_too() -> None:
    await _insert("mine", "Cindy", DEFAULT_OWNER_ID, category="girls")
    await _insert("theirs", "Not Mine", "another-user", category="girls")

    assert [doc["id"] for doc in await search_workouts(category="girls")] == ["mine"]


async def test_search_treats_the_query_literally() -> None:
    await _insert("mine", "5+ rounds", DEFAULT_OWNER_ID)

    assert [doc["id"] for doc in await search_workouts(query="5+")] == ["mine"]
    assert await search_workouts(query=".*") == []


async def test_get_workout_refuses_another_owners_id() -> None:
    await _insert("theirs", "Not Mine", "another-user")

    assert await get_workout("theirs") is None


async def test_created_workouts_are_owned() -> None:
    """An unowned write would be invisible to the web app's library."""
    created = await create_timer_workout(
        name="Ladder",
        mode="interval",
        segments=[{"work_seconds": 300, "movements": [{"name": "Row"}]}],
    )

    doc = await get_workouts_collection().find_one({"_id": created["id"]})
    assert doc is not None
    assert doc["owner_id"] == DEFAULT_OWNER_ID

    # ...and it comes back through the server's own read path.
    assert (await get_workout(created["id"]))["name"] == "Ladder"  # type: ignore[index]


async def test_owner_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment with real accounts points the server at one of them."""
    monkeypatch.setenv("MCP_OWNER_ID", "someone-else")
    get_settings.cache_clear()

    await _insert("default-user", "Fran", DEFAULT_OWNER_ID)
    created = await create_timer_workout(name="Fran", mode="for_time", segments=[])

    assert [doc["id"] for doc in await search_workouts(query="Fran")] == [created["id"]]
    assert await get_workout("default-user") is None


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
    created = await create_timer_workout(name="Chelsea", mode="emom", segments=[])

    assert created["interval_clock"] == "count_down"
