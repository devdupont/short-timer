from short_timer.models import Movement, Workout, WorkoutMode, WorkoutSegment


def test_workout_gets_a_generated_id() -> None:
    workout = Workout(name="Fran", mode=WorkoutMode.FOR_TIME)
    assert workout.id
    assert workout.segments == []


def test_nested_segment_rounds_survive_round_trip() -> None:
    workout = Workout(
        name="Murph",
        mode=WorkoutMode.FOR_TIME,
        category="hero",
        segments=[
            WorkoutSegment(label="Run", movements=[Movement(name="Run", distance="1 mile")]),
            WorkoutSegment(
                label="Partition",
                rounds=20,
                movements=[
                    Movement(name="Pull-up", reps=5),
                    Movement(name="Push-up", reps=10),
                    Movement(name="Air Squat", reps=15),
                ],
            ),
            WorkoutSegment(label="Run", movements=[Movement(name="Run", distance="1 mile")]),
        ],
    )

    restored = Workout.model_validate(workout.model_dump(mode="json"))
    assert restored.segments[1].rounds == 20
    assert restored.segments[1].movements[1].reps == 10


def test_per_segment_durations_survive_round_trip() -> None:
    """A ladder is legs of differing length, which needs per-segment durations."""
    workout = Workout(
        name="5/4/3/2/1 minutes with 2 minutes rest",
        mode=WorkoutMode.INTERVAL,
        rounds=1,
        segments=[
            WorkoutSegment(label=f"Interval {i}", work_seconds=seconds, rest_seconds=120)
            for i, seconds in enumerate([300, 240, 180, 120, 60], start=1)
        ],
    )

    restored = Workout.model_validate(workout.model_dump(mode="json"))
    assert [s.work_seconds for s in restored.segments] == [300, 240, 180, 120, 60]
    assert {s.rest_seconds for s in restored.segments} == {120}


def test_segment_durations_default_to_unset() -> None:
    """A uniform interval workout leaves them alone and uses the workout-level pair."""
    segment = WorkoutSegment(movements=[Movement(name="Row")])
    assert segment.work_seconds is None
    assert segment.rest_seconds is None


def test_a_segment_that_only_says_rest_is_a_rest_leg() -> None:
    """An EMOM's rest minute arrives as a movement; the clock needs the flag."""
    assert WorkoutSegment(movements=[Movement(name="Rest")]).is_rest
    assert WorkoutSegment(label="Rest", movements=[]).is_rest
    assert WorkoutSegment(movements=[Movement(name="Rest.", notes="walk it off")]).is_rest


def test_work_is_never_reinterpreted_as_rest() -> None:
    assert not WorkoutSegment(movements=[Movement(name="Row", calories=16)]).is_rest
    # A named movement wins over a label — "rest round" still has rows in it.
    assert not WorkoutSegment(label="Rest", movements=[Movement(name="Renegade Row")]).is_rest
    # Rest is never *inferred* from an empty segment, only from something saying so.
    assert not WorkoutSegment(movements=[]).is_rest
    assert not WorkoutSegment(movements=[Movement(reps=10)]).is_rest


def test_rest_leg_survives_round_trip() -> None:
    """The EMOM shape this exists for: four working minutes, then one off."""
    workout = Workout(
        name="15:00 EMOM",
        mode=WorkoutMode.EMOM,
        rounds=3,
        work_seconds=60,
        segments=[
            WorkoutSegment(movements=[Movement(name="Row", calories=16)]),
            WorkoutSegment(movements=[Movement(name="GHD Sit-Up", reps=16)]),
            WorkoutSegment(movements=[Movement(name="Wall Walk", reps=3)]),
            WorkoutSegment(movements=[Movement(name="Renegade Row", reps=16, load="50/35 lb")]),
            WorkoutSegment(is_rest=True),
        ],
    )

    restored = Workout.model_validate(workout.model_dump(mode="json"))
    assert [s.is_rest for s in restored.segments] == [False, False, False, False, True]
