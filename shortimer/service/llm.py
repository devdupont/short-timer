"""Uses the Anthropic API to turn free-form workout text into a `Workout`.

The model is forced to call a single tool (`emit_workout`) whose input schema
mirrors `Workout`, so the response is always valid structured data rather than
prose we'd have to parse ourselves.
"""

from __future__ import annotations

from functools import lru_cache

from anthropic import AsyncAnthropic
from anthropic.types import Message, ToolChoiceToolParam, ToolParam

from shortimer.config import get_settings
from shortimer.model.workout import Workout

SYSTEM_PROMPT = """\
You convert workout descriptions (often CrossFit-style WODs) into a \
structured timer format. Read the workout text carefully and identify:

- `mode`: how the clock should run.
  - "for_time": complete the work as fast as possible, optionally against a \
time cap. Includes classic chippers and "N rounds for time".
  - "amrap": as many rounds/reps as possible within a fixed time window.
  - "emom": every minute (or every N seconds) on the minute, perform a task. \
"Every 3:00 x 5 sets" is this too — a fixed cadence the next set starts on, \
where whatever time the work leaves over is the rest.
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

When the legs of an interval workout are *not all the same length* — a ladder \
or pyramid like "5/4/3/2/1 minutes with 2 minutes rest", or "2/3/2/3/2/3/2 \
minutes with 1 minute rest" — emit one segment per leg and put that leg's own \
duration in the segment's `work_seconds`, with the recovery that follows it in \
the segment's `rest_seconds`. Never write a duration into a movement's `notes` \
and leave `work_seconds` unset: the clock can only read the numeric fields, so \
a duration in prose is a duration the timer cannot run.

In that case the segments are a *sequence*, not a rotation, so `rounds` is how \
many times the whole sequence repeats — which is usually 1. Only use the \
workout-level `work_seconds`/`rest_seconds` when every leg really is the same \
length (e.g. "6 x 3 minutes with 2 minutes light" is `rounds` 6, \
`work_seconds` 180, `rest_seconds` 120, and needs no per-segment durations).

When one leg of the rotation is itself rest — an EMOM whose "Minute 5: Rest", \
a round that ends with a full minute off — set that segment's `is_rest` to true \
and leave its `movements` empty. Don't emit a movement named "Rest": the clock \
runs an `is_rest` segment as a rest period and says so, where a movement is \
something it tells the athlete to go do. A rest leg is a leg like any other, so \
it still counts toward the rotation and keeps the same duration as its \
neighbours — only give it `work_seconds` if that particular leg runs a \
different length. Use the workout-level `rest_seconds` instead when recovery \
follows *every* leg ("30 seconds on, 15 seconds off"); `is_rest` is for a leg \
of the rotation that is nothing but rest.

Set `interval_clock` to "count_up" when an interval workout is scored by how \
long each set took — "Every 3:00 x 5 sets ... score = slowest set time", "each \
round for time", "record your time for every set". Athletes each finish at a \
different moment, so the clock has to show elapsed time within the set for \
them to read their own; the window still runs its normal length and the rest \
of the leg is still recovery. Leave it alone (counting down) for an ordinary \
EMOM or tabata, where what matters is the time left to finish the work. Note \
this is about *timed sets*, not about a scored workout in general: an AMRAP \
scored by rounds, or a "record your reps" EMOM, still counts down.

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
            "interval_clock": {
                "type": "string",
                "enum": ["count_down", "count_up"],
                "description": (
                    "Which way the clock runs inside one interval leg. Default "
                    '"count_down" (time left to finish the minute). Use '
                    '"count_up" when each set is scored by its finish time, so '
                    "each athlete can read their own split off the clock."
                ),
            },
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "rounds": {"type": "integer"},
                        "rep_scheme": {"type": "array", "items": {"type": "integer"}},
                        "work_seconds": {
                            "type": "integer",
                            "description": "This leg's own work duration, when the legs of the "
                            "workout differ in length. Falls back to the workout-level value.",
                        },
                        "rest_seconds": {
                            "type": "integer",
                            "description": "Recovery following this leg, when the legs of the "
                            "workout differ in length. Falls back to the workout-level value.",
                        },
                        "is_rest": {
                            "type": "boolean",
                            "description": "True when this leg is rest rather than work — an "
                            'EMOM minute that just says "Rest". Leave `movements` empty; the '
                            "leg keeps its normal duration.",
                        },
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


async def _record_usage(response: Message, *, owner_id: str | None, purpose: str) -> None:
    """Log the token cost of one call to the metrics stream.

    The cache fields are read even though prompt caching isn't switched on yet
    — they report zero until it is, and reading them now means the day someone
    adds a `cache_control` marker the saving shows up in the numbers without a
    second change. Imported here rather than at module scope to keep `metrics`
    off the import path of the MCP server and the scraper, neither of which has
    a database.
    """
    from shortimer.metrics import record_model_call

    usage = getattr(response, "usage", None)
    if usage is None:
        return
    await record_model_call(
        model=str(getattr(response, "model", "") or get_settings().anthropic_model),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        owner_id=owner_id,
        purpose=purpose,
    )


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


async def parse_workout_text(
    text: str,
    name_hint: str | None = None,
    *,
    owner_id: str | None = None,
    purpose: str = "parse",
) -> Workout:
    """Turn workout text into a `Workout`, and record what the call cost.

    `owner_id` and `purpose` exist only for the metrics event. Instrumenting
    here rather than at each of the six call sites means a new one can't
    forget: this function *is* the moment money is spent, so it's the only
    place that can't miss a call.
    """
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

    # Before the response is inspected: the tokens were spent whether or not
    # the payload turns out to be usable, and a parse that fails validation is
    # exactly the kind of waste worth being able to see.
    await _record_usage(response, owner_id=owner_id, purpose=purpose)

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
