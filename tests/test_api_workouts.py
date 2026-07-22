import pytest
from httpx import ASGITransport, AsyncClient

from short_timer.app import app
from short_timer.dedup import source_hash
from short_timer.models import Movement, Workout, WorkoutMode, WorkoutSegment


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def authed_client(client: AsyncClient) -> AsyncClient:
    response = await client.post("/api/auth/login", json={"passcode": "test-passcode"})
    assert response.status_code == 204
    return client


def _fran() -> Workout:
    return Workout(
        name="Fran",
        mode=WorkoutMode.FOR_TIME,
        rep_scheme=[21, 15, 9],
        segments=[
            WorkoutSegment(
                movements=[Movement(name="Thruster", load="95/65 lb"), Movement(name="Pull-up")]
            )
        ],
    )


async def test_login_requires_correct_passcode(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"passcode": "wrong"})
    assert response.status_code == 401


async def test_workouts_require_auth(client: AsyncClient) -> None:
    response = await client.get("/api/workouts")
    assert response.status_code == 401


async def test_login_then_crud_flow(authed_client: AsyncClient) -> None:
    created = await authed_client.post(
        "/api/workouts", json={"workout": _fran().model_dump(mode="json")}
    )
    assert created.status_code == 201
    workout_id = created.json()["id"]

    listed = await authed_client.get("/api/workouts")
    assert listed.status_code == 200
    assert any(w["id"] == workout_id for w in listed.json())

    fetched = await authed_client.get(f"/api/workouts/{workout_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Fran"

    updated_payload = fetched.json()
    updated_payload["description"] = "21-15-9 thrusters and pull-ups"
    updated = await authed_client.put(
        f"/api/workouts/{workout_id}", json={"workout": updated_payload}
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "21-15-9 thrusters and pull-ups"

    deleted = await authed_client.delete(f"/api/workouts/{workout_id}")
    assert deleted.status_code == 204

    missing = await authed_client.get(f"/api/workouts/{workout_id}")
    assert missing.status_code == 404


async def test_get_unknown_workout_is_404(authed_client: AsyncClient) -> None:
    response = await authed_client.get("/api/workouts/does-not-exist")
    assert response.status_code == 404


async def test_parse_endpoint_uses_llm_parser(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_parse(text: str, name_hint: str | None = None) -> Workout:
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", fake_parse)

    response = await authed_client.post("/api/workouts/parse", json={"text": "21-15-9 Fran"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Fran"
    assert body["rep_scheme"] == [21, 15, 9]


async def test_create_is_idempotent_by_source_text(authed_client: AsyncClient) -> None:
    payload = _fran().model_copy(update={"source_text": "Fran\n21-15-9\nThrusters\nPull-ups"})
    first = await authed_client.post(
        "/api/workouts", json={"workout": payload.model_dump(mode="json")}
    )
    assert first.status_code == 201

    # Saving the same source text again returns the existing record, not a dupe.
    second_payload = _fran().model_copy(
        update={"source_text": "fran\n  21-15-9\nthrusters\npull-ups\n"}  # whitespace/case noise
    )
    second = await authed_client.post(
        "/api/workouts", json={"workout": second_payload.model_dump(mode="json")}
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]

    listed = await authed_client.get("/api/workouts")
    assert len(listed.json()) == 1


async def test_parse_reuses_saved_workout_without_calling_llm(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "Cindy\nAMRAP 20\n5 pull-ups\n10 push-ups\n15 air squats"
    saved = await authed_client.post(
        "/api/workouts",
        json={"workout": _fran().model_copy(update={"source_text": text}).model_dump(mode="json")},
    )
    saved_id = saved.json()["id"]

    async def exploding_parse(text: str, name_hint: str | None = None) -> Workout:
        raise AssertionError("LLM should not be called for a cached workout")

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", exploding_parse)

    response = await authed_client.post("/api/workouts/parse", json={"text": text})
    assert response.status_code == 200
    assert response.json()["id"] == saved_id


async def test_backfill_restores_dedup_for_legacy_rows(authed_client: AsyncClient) -> None:
    """Rows saved before source_hash existed must still dedupe once backfilled."""
    from short_timer.db import backfill_source_hashes, get_workouts_collection

    text = "Cindy\nAMRAP 20 minutes:\n5 pull-ups\n10 push-ups\n15 air squats"
    created = await authed_client.post(
        "/api/workouts",
        json={"workout": _fran().model_copy(update={"source_text": text}).model_dump(mode="json")},
    )
    legacy_id = created.json()["id"]
    # Simulate a document written before the field was introduced.
    await get_workouts_collection().update_one({"_id": legacy_id}, {"$unset": {"source_hash": ""}})

    assert await backfill_source_hashes() == 1

    # Same text, different capitalization — now correctly matches the legacy row.
    again = await authed_client.post(
        "/api/workouts",
        json={
            "workout": _fran()
            .model_copy(update={"source_text": text.upper()})
            .model_dump(mode="json")
        },
    )
    assert again.json()["id"] == legacy_id
    assert len((await authed_client.get("/api/workouts")).json()) == 1


async def test_another_owners_workout_is_invisible(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every read/write path is owner-scoped, ready for real accounts."""
    from short_timer.db import get_workouts_collection

    text = "Someone else's workout\n21-15-9"
    await get_workouts_collection().insert_one(
        {
            "_id": "not-mine",
            "owner_id": "another-user",
            "name": "Not Mine",
            "mode": "for_time",
            "segments": [],
            "source_text": text,
            "source_hash": source_hash(text),
            "created_at": "2026-07-19T00:00:00Z",
            "updated_at": "2026-07-19T00:00:00Z",
        }
    )

    assert (await authed_client.get("/api/workouts")).json() == []
    assert (await authed_client.get("/api/workouts/not-mine")).status_code == 404
    assert (await authed_client.delete("/api/workouts/not-mine")).status_code == 404

    # Dedup must not hand us another owner's record for identical text — it
    # should miss the cache and parse fresh instead.
    async def fake_parse(text: str, name_hint: str | None = None) -> Workout:
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", fake_parse)

    parsed = await authed_client.post("/api/workouts/parse", json={"text": text})
    assert parsed.status_code == 200
    assert parsed.json()["id"] != "not-mine"


async def test_loading_a_prewarmed_wod_costs_no_llm_call(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WOD parsed by the daily task is copied, not re-parsed, per user."""
    from short_timer.parse_cache import remember_parse

    text = "50-40-30-20-10 reps for time of:\nDouble-unders\nSit-ups"
    # What the daily background task leaves behind in the shared pool.
    await remember_parse(Workout(name="Sunday 260719", mode=WorkoutMode.FOR_TIME, source_text=text))

    async def exploding_parse(text: str, name_hint: str | None = None) -> Workout:
        raise AssertionError("a pre-parsed WOD must not hit the model")

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", exploding_parse)

    saved = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert saved.status_code == 201
    assert saved.json()["name"] == "Sunday 260719"

    # Saved into this owner's library, and repeat loads stay free.
    listed = await authed_client.get("/api/workouts")
    assert len(listed.json()) == 1
    again = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert again.json()["id"] == saved.json()["id"]


async def test_parse_is_shared_across_owners(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One user's parse spares every other user the same LLM call."""
    from short_timer.app import app as fastapi_app
    from short_timer.auth import current_owner

    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None) -> Workout:
        nonlocal calls
        calls += 1
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", counting_parse)

    text = "Helen\n3 rounds for time:\n400m run\n21 kettlebell swings\n12 pull-ups"
    first = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert first.status_code == 201
    assert calls == 1

    # Same text, different owner: served from the shared pool, no second call.
    fastapi_app.dependency_overrides[current_owner] = lambda: "second-user"
    try:
        second = await authed_client.post("/api/workouts/from-text", json={"text": text})
        assert second.status_code == 201
        assert calls == 1
        # Their own copy, not a pointer at the first user's record.
        assert second.json()["id"] != first.json()["id"]
    finally:
        fastapi_app.dependency_overrides.pop(current_owner, None)


async def test_one_owners_edits_do_not_leak_to_another(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool shares the neutral parse, never a user's customizations."""
    from short_timer.app import app as fastapi_app
    from short_timer.auth import current_owner

    async def fake_parse(text: str, name_hint: str | None = None) -> Workout:
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", fake_parse)

    text = "Murph\nFor time:\n1 mile run\n100 pull-ups"
    created = await authed_client.post("/api/workouts/from-text", json={"text": text})
    workout_id = created.json()["id"]

    # First owner customizes their copy: renames it and adds a time cap.
    edited = created.json() | {"name": "Murph — masters scaling", "time_cap_seconds": 3600}
    updated = await authed_client.put(f"/api/workouts/{workout_id}", json={"workout": edited})
    assert updated.status_code == 200

    # A second owner pasting the same text gets the neutral parse.
    fastapi_app.dependency_overrides[current_owner] = lambda: "second-user"
    try:
        theirs = await authed_client.post("/api/workouts/from-text", json={"text": text})
        assert theirs.json()["name"] == "Fran"  # the parse, not the rename
        assert theirs.json()["time_cap_seconds"] is None
    finally:
        fastapi_app.dependency_overrides.pop(current_owner, None)


async def test_seed_benchmarks_is_idempotent(authed_client: AsyncClient) -> None:
    first = await authed_client.post("/api/workouts/seed")
    assert first.status_code == 200
    assert first.json()["added"] > 0
    assert first.json()["skipped"] == 0

    listed = await authed_client.get("/api/workouts")
    names = {w["name"] for w in listed.json()}
    assert {"Murph", "Cindy", "Fran"} <= names
    assert len(listed.json()) == first.json()["added"]

    # Seeding again adds nothing and doesn't duplicate.
    second = await authed_client.post("/api/workouts/seed")
    assert second.json() == {"added": 0, "skipped": first.json()["added"]}
    assert len((await authed_client.get("/api/workouts")).json()) == first.json()["added"]


async def test_from_text_creates_then_caches(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None) -> Workout:
        nonlocal calls
        calls += 1
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", counting_parse)

    text = "Fran\n21-15-9 thrusters and pull-ups"
    first = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert first.status_code == 201
    second = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert second.status_code == 201

    assert first.json()["id"] == second.json()["id"]
    assert calls == 1  # parsed once, reused thereafter
    listed = await authed_client.get("/api/workouts")
    assert len(listed.json()) == 1
