"""MCP server exposing short-timer's workout authoring and library.

Two groups of tools:

- Authoring: `parse_workout_text` runs the same LLM parser the web app uses;
  `create_timer_workout` lets a client that already knows the structure
  (e.g. an LLM that read this docstring) build and save a `Workout` directly,
  without a second round-trip through our parser.
- Library: `search_workouts` and `get_workout` read from the same MongoDB
  collection the web app writes to, so any MCP client can pull a saved or
  benchmark workout (Murph, Fran, ...) by name.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from short_timer.db import get_workouts_collection
from short_timer.llm import parse_workout_text
from short_timer.models import Movement, Workout, WorkoutMode, WorkoutSegment

mcp = FastMCP("short-timer")


def _to_document(workout: Workout) -> dict[str, Any]:
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
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
    `{"label": str?, "rounds": int?, "rep_scheme": [int]?, "work_seconds": int?,
    "rest_seconds": int?, "is_rest": bool?, "movements": [...]}`
    where each movement is `{"name": str, "reps": int?, "distance": str?,
    "calories": int?, "load": str?, "notes": str?}`.

    A segment's `work_seconds`/`rest_seconds` override the workout-level pair
    for that leg alone, which is how a ladder ("5/4/3/2/1 minutes") is built.
    `is_rest` marks a leg that is itself the recovery — an EMOM minute that
    just says "Rest" — so the clock runs it as a rest period; give it no
    movements.
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
                work_seconds=segment.get("work_seconds"),
                rest_seconds=segment.get("rest_seconds"),
                is_rest=bool(segment.get("is_rest", False)),
                movements=[Movement(**m) for m in segment.get("movements", [])],
            )
            for segment in segments
        ],
    )
    collection = get_workouts_collection()
    await collection.insert_one(_to_document(workout))
    return workout.model_dump(mode="json")


@mcp.tool()
async def search_workouts(query: str = "", category: str | None = None) -> list[dict[str, Any]]:
    """Search the saved workout library by name/description substring and/or category."""
    mongo_query: dict[str, Any] = {}
    if query:
        mongo_query["$or"] = [
            {"name": {"$regex": query, "$options": "i"}},
            {"description": {"$regex": query, "$options": "i"}},
        ]
    if category:
        mongo_query["category"] = category

    collection = get_workouts_collection()
    return [_from_document(doc) async for doc in collection.find(mongo_query).limit(25)]


@mcp.tool()
async def get_workout(workout_id: str) -> dict[str, Any] | None:
    """Fetch a single saved workout by id."""
    collection = get_workouts_collection()
    doc = await collection.find_one({"_id": workout_id})
    return _from_document(doc) if doc else None


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
