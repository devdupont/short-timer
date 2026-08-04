"""The MCP tools are owner-scoped the same way the HTTP routes are.

The threat these cover is specific: an MCP tool's arguments are chosen by a
model from whatever is in its context, so anything that decides *whose* data a
call touches must come from configuration instead.
"""

import pytest

from short_timer import mcp_server
from short_timer.auth import DEFAULT_OWNER_ID
from short_timer.config import get_settings
from short_timer.db import get_workouts_collection
from short_timer.models import Workout, WorkoutMode
from short_timer.search import search_text


@pytest.fixture(autouse=True)
def _fresh_settings() -> None:
    """Settings are cached process-wide; drop it so env overrides take effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _insert(owner_id: str, name: str, **fields: object) -> str:
    """Write a workout straight to the collection, as another client would."""
    workout = Workout(name=name, mode=WorkoutMode.FOR_TIME, **fields)  # type: ignore[arg-type]
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = owner_id
    doc["search_text"] = search_text(workout)
    await get_workouts_collection().insert_one(doc)
    return workout.id


async def test_created_workouts_belong_to_the_configured_owner() -> None:
    result = await mcp_server.create_timer_workout(
        name="Fran", mode="for_time", segments=[{"movements": [{"name": "Thruster"}]}]
    )

    doc = await get_workouts_collection().find_one({"_id": result["id"]})
    assert doc is not None
    assert doc["owner_id"] == DEFAULT_OWNER_ID
    # Searchable immediately, rather than waiting on a startup backfill.
    assert "thruster" in doc["search_text"]


async def test_search_returns_only_the_configured_owners_workouts() -> None:
    mine = await _insert(DEFAULT_OWNER_ID, "Fran")
    await _insert("another-user", "Fran")

    found = await mcp_server.search_workouts(query="fran")
    assert [w["id"] for w in found] == [mine]


async def test_get_workout_will_not_read_another_owners_record() -> None:
    theirs = await _insert("another-user", "Not Mine")
    assert await mcp_server.get_workout(theirs) is None

    mine = await _insert(DEFAULT_OWNER_ID, "Mine")
    fetched = await mcp_server.get_workout(mine)
    assert fetched is not None and fetched["name"] == "Mine"


async def test_owner_follows_configuration_not_tool_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pointing the process at an account is the *only* way to change tenancy."""
    await _insert("gym-owner", "Theirs")
    assert await mcp_server.search_workouts(query="theirs") == []

    monkeypatch.setenv("MCP_OWNER_ID", "gym-owner")
    get_settings.cache_clear()

    found = await mcp_server.search_workouts(query="theirs")
    assert [w["name"] for w in found] == ["Theirs"]


async def test_search_treats_the_query_as_text_not_a_pattern() -> None:
    """`query` reaches a regex, and a model can put anything in it."""
    await _insert(DEFAULT_OWNER_ID, "Fran")

    assert await mcp_server.search_workouts(query=".*") == []
    # Catastrophic backtracking if this were compiled as a pattern.
    assert await mcp_server.search_workouts(query="(a+)+b") == []
    # An unbalanced group is a regex compile error, not an empty result.
    assert await mcp_server.search_workouts(query="fran(") == []


async def test_search_matches_movements_and_filters_by_category() -> None:
    await _insert(DEFAULT_OWNER_ID, "Cindy", category="benchmark")
    await _insert(DEFAULT_OWNER_ID, "Homemade", category="custom")

    assert [w["name"] for w in await mcp_server.search_workouts(category="custom")] == ["Homemade"]
    assert await mcp_server.search_workouts(query="cindy", category="custom") == []


async def test_search_is_bounded() -> None:
    """A tool result is fed back to a model as tokens, so it can't be unbounded."""
    for i in range(mcp_server.SEARCH_LIMIT + 5):
        await _insert(DEFAULT_OWNER_ID, f"Workout {i:02d}")

    assert len(await mcp_server.search_workouts()) == mcp_server.SEARCH_LIMIT
