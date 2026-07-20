"""Uses the Anthropic API to turn free-form workout text into a `Workout`.

The model is forced to call a single tool (`emit_workout`) whose input schema
mirrors `Workout`, so the response is always valid structured data rather than
prose we'd have to parse ourselves.
"""

from __future__ import annotations

from functools import lru_cache

from anthropic import AsyncAnthropic
from anthropic.types import ToolChoiceToolParam, ToolParam

from short_timer.config import get_settings
from short_timer.models import Workout

SYSTEM_PROMPT = """\
You convert workout descriptions (often CrossFit-style WODs) into a \
structured timer format. Read the workout text carefully and identify:

- `mode`: how the clock should run.
  - "for_time": complete the work as fast as possible, optionally against a \
time cap. Includes classic chippers and "N rounds for time".
  - "amrap": as many rounds/reps as possible within a fixed time window.
  - "emom": every minute (or every N seconds) on the minute, perform a task.
  - "tabata": fixed work/rest intervals repeated for a number of rounds \
(classically 20s work / 10s rest x 8).
  - "interval": other fixed work/rest interval schemes that aren't tabata.
  - "custom": anything that doesn't fit the above (e.g. a rest day, a \
strength/skill session with no clock).
- `time_cap_seconds`: the overall time cap (for_time) or window (amrap), in \
seconds, if stated.
- `rounds`: number of rounds, if the whole workout repeats as a block (e.g. \
"5 rounds for time", or the interval count for emom/tabata/interval).
- `work_seconds` / `rest_seconds`: per-interval work and rest duration, for \
emom/tabata/interval.
- `rep_scheme`: a whole-workout descending/ascending rep ladder like \
21-15-9, if present.
- `segments`: the ordered list of movements to perform. Nest a segment's own \
`rounds` and `rep_scheme` when a *part* of the workout repeats independently \
of the overall structure (e.g. Murph's "20 rounds of 5 pull-ups, 10 \
push-ups, 15 air squats" nested between two 1-mile runs).

Always call `emit_workout` exactly once with your best-effort structured \
reading of the workout. Do not invent movements or numbers that aren't \
implied by the text. Some interval workouts (e.g. "30 seconds on, 15 \
seconds rest") don't name a specific exercise at all — when that's the \
case, omit the movement's `name` rather than inventing a placeholder like \
"unknown".\
"""

_WORKOUT_TOOL: ToolParam = {
    "name": "emit_workout",
    "description": "Record the structured, timer-ready form of a workout.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short name for the workout."},
            "description": {
                "type": "string",
                "description": "One-line human-readable summary of the workout.",
            },
            "category": {
                "type": "string",
                "description": (
                    "e.g. benchmark, open, custom. Never categorize by gender — "
                    'no "girl"/"boy" labels. Scaling differences between athletes '
                    "belong in each movement's reps and loads, not the category."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["for_time", "amrap", "emom", "tabata", "interval", "custom"],
            },
            "time_cap_seconds": {"type": "integer"},
            "rounds": {"type": "integer"},
            "work_seconds": {"type": "integer"},
            "rest_seconds": {"type": "integer"},
            "rep_scheme": {"type": "array", "items": {"type": "integer"}},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "rounds": {"type": "integer"},
                        "rep_scheme": {"type": "array", "items": {"type": "integer"}},
                        "movements": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Omit if the workout doesn't name a "
                                        "specific exercise.",
                                    },
                                    "reps": {"type": "integer"},
                                    "distance": {"type": "string"},
                                    "calories": {"type": "integer"},
                                    "load": {"type": "string"},
                                    "notes": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["movements"],
                },
            },
        },
        "required": ["name", "mode", "segments"],
    },
}


class WorkoutParseError(RuntimeError):
    """Raised when the LLM's response can't be turned into a `Workout`."""


@lru_cache
def _client() -> AsyncAnthropic:
    """One client, reused across requests.

    Rebuilding it per call throws away the connection pool. The explicit
    timeout matters more: the SDK defaults to ten minutes, long enough for a
    single hung call to hold a request — and a container replica — open well
    past the point the caller has given up.
    """
    settings = get_settings()
    return AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
    )


async def parse_workout_text(text: str, name_hint: str | None = None) -> Workout:
    settings = get_settings()
    client = _client()

    user_content = text if not name_hint else f"Workout name: {name_hint}\n\n{text}"
    tool_choice: ToolChoiceToolParam = {"type": "tool", "name": "emit_workout"}

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[_WORKOUT_TOOL],
        tool_choice=tool_choice,
        messages=[{"role": "user", "content": user_content}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise WorkoutParseError("Model did not return a structured workout.")

    try:
        payload = dict(tool_use.input)
        payload["source_text"] = text
        return Workout(**payload)
    except Exception as exc:  # pydantic ValidationError or malformed payload
        raise WorkoutParseError(
            f"Could not build a Workout from the model's response: {exc}"
        ) from exc
