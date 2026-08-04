"""MCP server exposing short-timer's workout authoring and library.

Two groups of tools:

- Authoring: `parse_workout_text` runs the same LLM parser the web app uses;
  `create_timer_workout` lets a client that already knows the structure
  (e.g. an LLM that read this docstring) build and save a `Workout` directly,
  without a second round-trip through our parser.
- Library: `search_workouts` and `get_workout` read from the same MongoDB
  collection the web app writes to, so any MCP client can pull a saved or
  benchmark workout (Murph, Fran, ...) by name.

Every tool here is scoped to one account, exactly like the web app's routes.
See `_owner_id` for where that account comes from.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from short_timer.auth import DEFAULT_OWNER_ID
from short_timer.config import get_settings
from short_timer.db import get_workouts_collection
from short_timer.llm import parse_workout_text
from short_timer.models import Movement, Workout, WorkoutMode, WorkoutSegment
from short_timer.search import library_query, search_text

mcp = FastMCP("short-timer")

#: Most workouts a search will return at once. A tool result is fed back to a
#: model as tokens, so this is a context budget as much as a query limit.
SEARCH_LIMIT = 25


def _owner_id() -> str:
    """The account these tools act as.

    The single place tenancy is decided here, mirroring `current_owner` on the
    web side. An MCP process has no session to carry a user id, so it comes
    from configuration: one process serves one person.

    Deliberately *not* a tool argument. A tool argument is chosen by the model
    from whatever it has in context, which would make reading another user's
    library a matter of guessing an id. Configuration can't be talked into a
    different value. When real accounts land, this becomes the signed-in
    account's id and no query below has to change.
    """
    return get_settings().mcp_owner_id or DEFAULT_OWNER_ID


def _to_document(workout: Workout, owner_id: str) -> dict[str, Any]:
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    # Library search filters on this field, so a workout saved here without it
    # would be missing from every search until the next startup backfill.
    doc["search_text"] = search_text(workout)
    # Ownership comes from configuration, never from a tool's arguments.
    doc["owner_id"] = owner_id
    return doc


def _from_document(doc: dict[str, Any]) -> dict[str, Any]:
    doc = dict(doc)
    doc["id"] = doc.pop("_id")
    return Workout(**doc).model_dump(mode="json")


@mcp.tool()
async def parse_workout(text: str, name_hint: str | None = None) -> dict[str, Any]:
    """Parse free-form workout text (e.g. a pasted WOD) into a timer-ready Workout."""
    workout = await parse_workout_text(text, name_hint=name_hint)
    return workout.model_dump(mode="json")


@mcp.tool()
async def create_timer_workout(
    name: str,
    mode: str,
    segments: list[dict[str, Any]],
    description: str | None = None,
    category: str | None = None,
    time_cap_seconds: int | None = None,
    rounds: int | None = None,
    work_seconds: int | None = None,
    rest_seconds: int | None = None,
    rep_scheme: list[int] | None = None,
) -> dict[str, Any]:
    """Build and save a Workout directly from structured fields.

    `segments` is a list of objects shaped like
    `{"label": str?, "rounds": int?, "rep_scheme": [int]?, "movements": [...]}`
    where each movement is `{"name": str, "reps": int?, "distance": str?,
    "calories": int?, "load": str?, "notes": str?}`.
    """
    workout = Workout(
        name=name,
        description=description,
        category=category,
        mode=WorkoutMode(mode),
        time_cap_seconds=time_cap_seconds,
        rounds=rounds,
        work_seconds=work_seconds,
        rest_seconds=rest_seconds,
        rep_scheme=rep_scheme,
        segments=[
            WorkoutSegment(
                label=segment.get("label"),
                rounds=segment.get("rounds"),
                rep_scheme=segment.get("rep_scheme"),
                movements=[Movement(**m) for m in segment.get("movements", [])],
            )
            for segment in segments
        ],
    )
    collection = get_workouts_collection()
    await collection.insert_one(_to_document(workout, _owner_id()))
    return workout.model_dump(mode="json")


@mcp.tool()
async def search_workouts(query: str = "", category: str | None = None) -> list[dict[str, Any]]:
    """Search the saved workout library by workout name, movement, mode, or category.

    Every word in `query` has to match. Matching is substring and
    case-insensitive, and the text is taken literally — punctuation in a
    workout name is not a pattern.
    """
    collection = get_workouts_collection()
    cursor = collection.find(library_query(_owner_id(), q=query, category=category)).sort(
        [("created_at", -1), ("_id", -1)]
    )
    return [_from_document(doc) async for doc in cursor.limit(SEARCH_LIMIT)]


@mcp.tool()
async def get_workout(workout_id: str) -> dict[str, Any] | None:
    """Fetch a single saved workout by id."""
    doc = await get_workouts_collection().find_one({"_id": workout_id, "owner_id": _owner_id()})
    return _from_document(doc) if doc else None


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
