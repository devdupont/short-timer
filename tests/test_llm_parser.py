"""Parser tests.

`test_parse_workout_text_*` mock the Anthropic client so the plumbing (system
prompt, forced tool choice, tool-response -> Workout) is verified without
hitting the network. `test_fixture_*` are the real test suite built from
scraped/curated workouts: they call the actual Anthropic API and check the
parser's output against each fixture's expectations. They're marked `live`
and skipped unless a real `ANTHROPIC_API_KEY` is configured, since they cost
tokens and need network access neither of which this repo's default test run
should depend on.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from short_timer import llm
from short_timer.models import IntervalClock, WorkoutMode

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "workouts.json"
FIXTURES: list[dict[str, Any]] = json.loads(FIXTURES_PATH.read_text())


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, tool_input: dict[str, Any]) -> None:
        self.input = tool_input


class _FakeMessage:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessages:
    def __init__(self, tool_input: dict[str, Any] | None, captured: dict[str, Any]) -> None:
        self._tool_input = tool_input
        self._captured = captured

    async def create(self, **kwargs: Any) -> _FakeMessage:
        self._captured.update(kwargs)
        content = [] if self._tool_input is None else [_FakeToolUseBlock(self._tool_input)]
        return _FakeMessage(content)


class _FakeAsyncAnthropic:
    def __init__(self, tool_input: dict[str, Any] | None, captured: dict[str, Any]) -> None:
        self.messages = _FakeMessages(tool_input, captured)


def _patch_anthropic(
    monkeypatch: pytest.MonkeyPatch, tool_input: dict[str, Any] | None
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        llm, "AsyncAnthropic", lambda **_: _FakeAsyncAnthropic(tool_input, captured)
    )
    return captured


async def test_parse_workout_text_forces_the_emit_workout_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_anthropic(
        monkeypatch,
        {"name": "Fran", "mode": "for_time", "rep_scheme": [21, 15, 9], "segments": []},
    )

    workout = await llm.parse_workout_text("21-15-9 thrusters and pull-ups")

    assert workout.name == "Fran"
    assert workout.mode is WorkoutMode.FOR_TIME
    assert workout.source_text == "21-15-9 thrusters and pull-ups"
    assert captured["tool_choice"] == {"type": "tool", "name": "emit_workout"}
    assert captured["tools"][0]["name"] == "emit_workout"


async def test_parse_flags_a_rest_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Either way the model writes a rest minute, it reaches the clock as rest."""
    _patch_anthropic(
        monkeypatch,
        {
            "name": "EMOM",
            "mode": "emom",
            "rounds": 3,
            "work_seconds": 60,
            "segments": [
                {"movements": [{"name": "Row", "calories": 16}]},
                {"is_rest": True, "movements": []},
                {"movements": [{"name": "Rest"}]},
            ],
        },
    )

    workout = await llm.parse_workout_text("15:00 EMOM")

    assert [s.is_rest for s in workout.segments] == [False, True, True]


async def test_parse_keeps_an_up_counting_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A set scored by its finish time has to reach the timer as counting up."""
    _patch_anthropic(
        monkeypatch,
        {
            "name": "Every 3:00 x 5 Sets",
            "mode": "interval",
            "rounds": 5,
            "work_seconds": 180,
            "interval_clock": "count_up",
            "segments": [{"movements": [{"name": "Rope Climb", "reps": 3}]}],
        },
    )

    workout = await llm.parse_workout_text("Every 3:00 x 5 Sets\nScore = Slowest Set Time")

    assert workout.interval_clock is IntervalClock.COUNT_UP


async def test_parse_defaults_to_counting_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_anthropic(monkeypatch, {"name": "Chelsea", "mode": "emom", "segments": []})

    workout = await llm.parse_workout_text("EMOM 30")

    assert workout.interval_clock is IntervalClock.COUNT_DOWN


async def test_workout_tool_offers_the_clock_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_anthropic(monkeypatch, {"name": "x", "mode": "interval", "segments": []})

    await llm.parse_workout_text("Every 3:00 x 5 Sets")

    schema = captured["tools"][0]["input_schema"]["properties"]["interval_clock"]
    assert schema["enum"] == ["count_down", "count_up"]


async def test_workout_tool_offers_is_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_anthropic(monkeypatch, {"name": "x", "mode": "emom", "segments": []})

    await llm.parse_workout_text("15:00 EMOM")

    segment_schema = captured["tools"][0]["input_schema"]["properties"]["segments"]["items"]
    assert "is_rest" in segment_schema["properties"]


async def test_parse_workout_text_raises_without_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_anthropic(monkeypatch, None)

    with pytest.raises(llm.WorkoutParseError):
        await llm.parse_workout_text("some workout")


async def test_parse_workout_text_raises_on_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_anthropic(monkeypatch, {"mode": "not-a-real-mode", "segments": []})

    with pytest.raises(llm.WorkoutParseError):
        await llm.parse_workout_text("some workout")


def _has_real_api_key() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return bool(key) and key != "test-anthropic-key"


@pytest.mark.live
@pytest.mark.skipif(not _has_real_api_key(), reason="needs a real ANTHROPIC_API_KEY")
@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["name"] for f in FIXTURES])
async def test_fixture_parses_to_expected_shape(fixture: dict[str, Any]) -> None:
    workout = await llm.parse_workout_text(fixture["source_text"], name_hint=fixture["name"])

    assert workout.mode.value == fixture["expected_mode"]

    if "expected_rep_scheme" in fixture:
        assert workout.rep_scheme == fixture["expected_rep_scheme"]
    if "expected_rounds" in fixture:
        assert workout.rounds == fixture["expected_rounds"]
    if "expected_time_cap_seconds" in fixture:
        assert workout.time_cap_seconds == fixture["expected_time_cap_seconds"]
    if "expected_work_seconds" in fixture:
        assert workout.work_seconds == fixture["expected_work_seconds"]
    if "expected_rest_seconds" in fixture:
        assert workout.rest_seconds == fixture["expected_rest_seconds"]
    if "expected_interval_clock" in fixture:
        # Whether the clock counts the set down or up is what an athlete reads
        # off the wall, so a workout scored by set times has to parse to the
        # direction that shows them their own split.
        assert workout.interval_clock.value == fixture["expected_interval_clock"]
    if "expected_min_segments" in fixture:
        assert len(workout.segments) >= fixture["expected_min_segments"]
    if "expected_rest_segments" in fixture:
        # A rest minute has to land as a rest *leg*, not as a movement called
        # "Rest" — that's the difference between the clock resting and the
        # clock telling an athlete to go do something named rest.
        rest_indexes = [i for i, s in enumerate(workout.segments) if s.is_rest]
        assert rest_indexes == fixture["expected_rest_segments"]
    if "expected_segment_work_seconds" in fixture:
        # A ladder's legs differ in length, so the durations have to land in the
        # segments — a duration left in prose is one the timer can't run.
        expected_legs = fixture["expected_segment_work_seconds"]
        assert [s.work_seconds for s in workout.segments] == expected_legs

    # Interval legs often name no movement at all ("30 seconds on, 15 off"), so
    # skip the unnamed ones rather than tripping over a None.
    all_movement_names = " | ".join(
        m.name.lower() for segment in workout.segments for m in segment.movements if m.name
    )
    for expected_movement in fixture["expected_movements"]:
        assert expected_movement in all_movement_names, (
            f"expected to find {expected_movement!r} in parsed movements: {all_movement_names!r}"
        )
