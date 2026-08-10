"""MCP server exposing shortimer's workout authoring and library.

Two groups of tools:

- Authoring: `parse_workout_text` runs the same LLM parser the web app uses;
  `create_timer_workout` lets a client that already knows the structure
  (e.g. an LLM that read this docstring) build and save a `Workout` directly,
  without a second round-trip through our parser.
- Library: `search_workouts` and `get_workout` read from the same MongoDB
  collection the web app writes to, so any MCP client can pull a saved or
  benchmark workout (Murph, Fran, ...) by name.

Every query here is scoped to one owner, and that owner comes from an API
token in `MCP_API_TOKEN` (mint one under Settings → API tokens). There's no
session to derive it from — this is a local stdio tool, not an HTTP caller —
which is also why it isn't the OAuth flow the MCP specification describes:
that's for HTTP transports, and stdio servers are told to take credentials
from the environment instead.

The token replaced an `MCP_OWNER_ID` setting that simply *named* an owner.
Naming one asserted who the server was acting as without proving it, so anyone
who could edit the environment could point it at any library. A token has to
have been issued to that account, carries scopes, and can be revoked on its
own.
"""

from __future__ import annotations

import re
from typing import Any

from mcp.server.mcpserver import MCPServer

from shortimer.auth.api_tokens import resolve_token
from shortimer.cache.db import get_workouts_collection
from shortimer.config import get_settings
from shortimer.model.token import ApiTokenScope
from shortimer.model.workout import (
    IntervalClock,
    Movement,
    Workout,
    WorkoutMode,
    WorkoutSegment,
)
from shortimer.service.llm import parse_workout_text

#: `MCPServer` is what the SDK's FastMCP became in mcp 2.0 — same decorator,
#: same schema-from-type-hints. The tools below are unchanged by the move.
mcp = MCPServer("shortimer")


class NotAuthorized(RuntimeError):
    """Raised when the configured token is missing, unknown, or too narrow.

    A plain exception rather than anything protocol-specific: the SDK turns one
    into a tool error the client can show, and there's nothing an MCP client
    could usefully *do* with a richer type — the fix is always to change the
    environment.
    """


async def _owner_for(scope: ApiTokenScope) -> str:
    """The library this server acts on, if the token allows this operation.

    Resolved per call rather than cached at startup, so revoking a token takes
    effect on the next tool call instead of at the next restart. It's one
    indexed lookup against a database this process is already talking to.
    """
    raw = get_settings().mcp_api_token
    if not raw:
        raise NotAuthorized("MCP_API_TOKEN is not set. Mint one under Settings → API tokens.")

    token = await resolve_token(raw)
    if token is None:
        raise NotAuthorized("MCP_API_TOKEN is not valid. It may have been revoked.")
    if scope not in token.scopes:
        raise NotAuthorized(f"This token does not carry the {scope.value} scope.")
    return token.user_id


def _to_document(workout: Workout, owner_id: str) -> dict[str, Any]:
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
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
    interval_clock: str = "count_down",
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

    `interval_clock` is "count_down" (the default: time left in the leg) or
    "count_up" for interval work scored by each set's finish time, where every
    athlete needs to read their own split off the clock.
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
        interval_clock=IntervalClock(interval_clock),
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
    owner_id = await _owner_for(ApiTokenScope.LIBRARY_WRITE)
    collection = get_workouts_collection()
    await collection.insert_one(_to_document(workout, owner_id))
    return workout.model_dump(mode="json")


@mcp.tool()
async def search_workouts(query: str = "", category: str | None = None) -> list[dict[str, Any]]:
    """Search the saved workout library by name/description substring and/or category."""
    mongo_query: dict[str, Any] = {"owner_id": await _owner_for(ApiTokenScope.LIBRARY_READ)}
    if query:
        # Escaped, so a query like "5+" is text to find rather than a pattern
        # for Mongo to run.
        pattern = re.escape(query)
        mongo_query["$or"] = [
            {"name": {"$regex": pattern, "$options": "i"}},
            {"description": {"$regex": pattern, "$options": "i"}},
        ]
    if category:
        mongo_query["category"] = category

    collection = get_workouts_collection()
    return [_from_document(doc) async for doc in collection.find(mongo_query).limit(25)]


@mcp.tool()
async def get_workout(workout_id: str) -> dict[str, Any] | None:
    """Fetch a single saved workout by id."""
    collection = get_workouts_collection()
    owner_id = await _owner_for(ApiTokenScope.LIBRARY_READ)
    doc = await collection.find_one({"_id": workout_id, "owner_id": owner_id})
    return _from_document(doc) if doc else None


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
