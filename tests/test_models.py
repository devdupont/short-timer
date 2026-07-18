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
