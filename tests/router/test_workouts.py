"""The workout library API: CRUD, parse-and-save, dedup, owner scoping, paging, search, filters,
and hardening against oversized input and upstream/unexpected errors."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import anthropic
import httpx
import pytest
from conftest import TEST_EMAIL
from httpx import ASGITransport, AsyncClient

from shortimer.app import app
from shortimer.config import get_settings
from shortimer.model.user import User
from shortimer.model.workout import (
    Movement,
    Workout,
    WorkoutMode,
    WorkoutSegment,
)
from shortimer.util.dedup import source_hash

SignInAs = Callable[[AsyncClient, str], Awaitable[str]]


def _fran() -> Workout:
    """A ready-to-save Fran, the fixture workout most of this file builds on."""
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


async def test_login_requires_the_correct_password(client: AsyncClient, account: User) -> None:
    """A wrong password is rejected."""
    response = await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "wrong"})
    assert response.status_code == 401


async def test_workouts_require_auth(client: AsyncClient) -> None:
    """An unauthenticated request to the library is rejected."""
    response = await client.get("/api/workouts")
    assert response.status_code == 401


async def test_login_then_crud_flow(authed_client: AsyncClient) -> None:
    """Create, list, read, update, and delete a workout, end to end."""
    created = await authed_client.post(
        "/api/workouts", json={"workout": _fran().model_dump(mode="json")}
    )
    assert created.status_code == 201
    workout_id = created.json()["id"]

    listed = await authed_client.get("/api/workouts")
    assert listed.status_code == 200
    assert any(w["id"] == workout_id for w in listed.json()["items"])

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
    """Fetching an id that doesn't exist reports 404."""
    response = await authed_client.get("/api/workouts/does-not-exist")
    assert response.status_code == 404


async def test_parse_endpoint_uses_llm_parser(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/parse` returns a preview built from the (mocked) parser's output, unsaved."""

    async def fake_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that returns a canned Fran carrying the given source text."""
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", fake_parse)

    response = await authed_client.post("/api/workouts/parse", json={"text": "21-15-9 Fran"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Fran"
    assert body["rep_scheme"] == [21, 15, 9]


async def test_create_is_idempotent_by_source_text(authed_client: AsyncClient) -> None:
    """Saving the same source text twice (modulo whitespace/case) returns the existing record."""
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
    assert listed.json()["total"] == 1


async def test_parse_reuses_saved_workout_without_calling_llm(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parsing text that matches an already-saved workout returns it instead of calling the model."""
    text = "Cindy\nAMRAP 20\n5 pull-ups\n10 push-ups\n15 air squats"
    saved = await authed_client.post(
        "/api/workouts",
        json={"workout": _fran().model_copy(update={"source_text": text}).model_dump(mode="json")},
    )
    saved_id = saved.json()["id"]

    async def exploding_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that fails the test if it's ever called."""
        raise AssertionError("LLM should not be called for a cached workout")

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", exploding_parse)

    response = await authed_client.post("/api/workouts/parse", json={"text": text})
    assert response.status_code == 200
    assert response.json()["id"] == saved_id


async def test_another_owners_workout_is_invisible(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every read/write path is owner-scoped, ready for real accounts."""
    from shortimer.cache.db import get_workouts_collection

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

    assert (await authed_client.get("/api/workouts")).json()["items"] == []
    assert (await authed_client.get("/api/workouts/not-mine")).status_code == 404
    assert (await authed_client.delete("/api/workouts/not-mine")).status_code == 404

    # Dedup must not hand us another owner's record for identical text — it
    # should miss the cache and parse fresh instead.
    async def fake_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that returns a canned Fran carrying the given source text."""
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", fake_parse)

    parsed = await authed_client.post("/api/workouts/parse", json={"text": text})
    assert parsed.status_code == 200
    assert parsed.json()["id"] != "not-mine"


async def test_loading_a_prewarmed_wod_costs_no_llm_call(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WOD parsed by the daily task is copied, not re-parsed, per user."""
    from shortimer.cache.parse import remember_parse

    text = "50-40-30-20-10 reps for time of:\nDouble-unders\nSit-ups"
    # What the daily background task leaves behind in the shared pool.
    await remember_parse(Workout(name="Sunday 260719", mode=WorkoutMode.FOR_TIME, source_text=text))

    async def exploding_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that fails the test if it's ever called."""
        raise AssertionError("a pre-parsed WOD must not hit the model")

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", exploding_parse)

    saved = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert saved.status_code == 201
    assert saved.json()["name"] == "Sunday 260719"

    # Saved into this owner's library, and repeat loads stay free.
    listed = await authed_client.get("/api/workouts")
    assert listed.json()["total"] == 1
    again = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert again.json()["id"] == saved.json()["id"]


async def test_parse_is_shared_across_owners(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One user's parse spares every other user the same LLM call."""
    from shortimer.app import app as fastapi_app
    from shortimer.auth.session import current_owner

    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake `parse_workout_text` that counts its own calls instead of hitting the model."""
        nonlocal calls
        calls += 1
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", counting_parse)

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
    from shortimer.app import app as fastapi_app
    from shortimer.auth.session import current_owner

    async def fake_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that returns a canned Fran carrying the given source text."""
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", fake_parse)

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
    """Seeding twice adds the benchmark library once and skips it the second time."""
    first = await authed_client.post("/api/workouts/seed")
    assert first.status_code == 200
    assert first.json()["added"] > 0
    assert first.json()["skipped"] == 0

    listed = await authed_client.get("/api/workouts")
    names = {w["name"] for w in listed.json()["items"]}
    assert {"Murph", "Cindy", "Fran"} <= names
    assert listed.json()["total"] == first.json()["added"]

    # Seeding again adds nothing and doesn't duplicate.
    second = await authed_client.post("/api/workouts/seed")
    assert second.json() == {"added": 0, "skipped": first.json()["added"]}
    assert (await authed_client.get("/api/workouts")).json()["total"] == first.json()["added"]


async def test_from_text_creates_then_caches(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/from-text` parses and saves on the first call, then returns the saved copy on the second."""
    calls = 0

    async def counting_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake `parse_workout_text` that counts its own calls instead of hitting the model."""
        nonlocal calls
        calls += 1
        return _fran().model_copy(update={"source_text": text})

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", counting_parse)

    text = "Fran\n21-15-9 thrusters and pull-ups"
    first = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert first.status_code == 201
    second = await authed_client.post("/api/workouts/from-text", json={"text": text})
    assert second.status_code == 201

    assert first.json()["id"] == second.json()["id"]
    assert calls == 1  # parsed once, reused thereafter
    listed = await authed_client.get("/api/workouts")
    assert listed.json()["total"] == 1


async def _save(client: AsyncClient, workout: Workout) -> str:
    """Save `workout` via the API and return its assigned id."""
    response = await client.post("/api/workouts", json={"workout": workout.model_dump(mode="json")})
    assert response.status_code == 201
    return str(response.json()["id"])


def _dated(name: str, day: int, **fields: object) -> Workout:
    """A workout with a fixed creation date, so listing order is deterministic."""
    created = datetime(2026, 1, day, tzinfo=UTC)
    return _fran().model_copy(
        update={"name": name, "created_at": created, "updated_at": created, **fields}
    )


async def test_list_pages_newest_first(authed_client: AsyncClient) -> None:
    """Paging through a 7-item library, 3 at a time, returns them newest-first, then empties out."""
    for day in range(1, 8):
        await _save(authed_client, _dated(f"Day {day}", day))

    first = await authed_client.get("/api/workouts", params={"limit": 3})
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 7
    assert body["limit"] == 3
    assert body["offset"] == 0
    assert [w["name"] for w in body["items"]] == ["Day 7", "Day 6", "Day 5"]

    middle = await authed_client.get("/api/workouts", params={"limit": 3, "offset": 3})
    assert [w["name"] for w in middle.json()["items"]] == ["Day 4", "Day 3", "Day 2"]

    # The last page is short, and the total still describes the whole library.
    last = await authed_client.get("/api/workouts", params={"limit": 3, "offset": 6})
    assert [w["name"] for w in last.json()["items"]] == ["Day 1"]
    assert last.json()["total"] == 7

    # Past the end is empty, not an error — a delete can leave a client here.
    past = await authed_client.get("/api/workouts", params={"limit": 3, "offset": 99})
    assert past.status_code == 200
    assert past.json()["items"] == []
    assert past.json()["total"] == 7


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 201}],
)
async def test_list_rejects_out_of_range_paging(
    authed_client: AsyncClient, params: dict[str, int | str]
) -> None:
    """An out-of-bounds limit, offset, or an overlong query string is rejected."""
    response = await authed_client.get("/api/workouts", params=params)
    assert response.status_code == 422


async def test_search_spans_the_whole_library_not_just_the_page(
    authed_client: AsyncClient,
) -> None:
    """The point of searching server-side: the match may be pages deep."""
    await _save(authed_client, _dated("Needle", 1))
    for day in range(2, 10):
        await _save(authed_client, _dated(f"Filler {day}", day))

    response = await authed_client.get("/api/workouts", params={"limit": 3, "q": "needle"})
    body = response.json()
    assert body["total"] == 1
    assert [w["name"] for w in body["items"]] == ["Needle"]


async def test_search_matches_the_fields_the_row_shows(authed_client: AsyncClient) -> None:
    """A search term matches name, category, movement, or mode label, and terms AND together."""
    await _save(authed_client, _dated("Fran", 1, category="benchmark"))
    await _save(
        authed_client,
        _dated(
            "Cindy",
            2,
            mode=WorkoutMode.AMRAP,
            category="girls",
            segments=[WorkoutSegment(movements=[Movement(name="Air squat")])],
        ),
    )

    async def names(query: str) -> list[str]:
        """Names of the workouts a search for `query` returns."""
        response = await authed_client.get("/api/workouts", params={"q": query})
        return [w["name"] for w in response.json()["items"]]

    assert await names("cindy") == ["Cindy"]  # name
    assert await names("girls") == ["Cindy"]  # category
    assert await names("squat") == ["Cindy"]  # a movement inside the workout
    assert await names("amrap") == ["Cindy"]  # the mode as the UI labels it
    assert await names("for time") == ["Fran"]  # ...including a two-word label
    assert await names("thruster") == ["Fran"]  # Fran's segments, untouched

    # Terms are AND-ed, so adding one narrows rather than widens.
    assert await names("squat girls") == ["Cindy"]
    assert await names("squat benchmark") == []


async def test_search_treats_the_query_literally(authed_client: AsyncClient) -> None:
    """Regex metacharacters are text to search for, not a pattern to run."""
    await _save(authed_client, _dated("Fran (scaled)", 1))
    await _save(authed_client, _dated("Helen", 2))

    matched = await authed_client.get("/api/workouts", params={"q": "(scaled)"})
    assert [w["name"] for w in matched.json()["items"]] == ["Fran (scaled)"]

    unmatched = await authed_client.get("/api/workouts", params={"q": ".*"})
    assert unmatched.json()["total"] == 0


async def test_filters_narrow_by_mode_and_category(authed_client: AsyncClient) -> None:
    """The mode and category filters combine with each other and with a search term."""
    await _save(authed_client, _dated("Fran", 1, category="benchmark"))
    await _save(authed_client, _dated("Grace", 2, category="girls"))
    await _save(authed_client, _dated("Cindy", 3, category="girls", mode=WorkoutMode.AMRAP))

    async def names(**params: str) -> list[str]:
        """Names of the workouts a request with `params` returns."""
        response = await authed_client.get("/api/workouts", params=params)
        assert response.status_code == 200
        return [w["name"] for w in response.json()["items"]]

    assert await names(mode="amrap") == ["Cindy"]
    assert await names(category="girls") == ["Cindy", "Grace"]
    # Filters combine with each other and with the search box.
    assert await names(mode="for_time", category="girls") == ["Grace"]
    assert await names(category="girls", q="cindy") == ["Cindy"]
    assert await names(category="girls", q="fran") == []


async def test_filters_narrow_the_total_not_just_the_page(authed_client: AsyncClient) -> None:
    """`total` drives the client's page count, so it has to follow the filters."""
    for day in range(1, 6):
        await _save(authed_client, _dated(f"Filler {day}", day, category="benchmark"))
    await _save(authed_client, _dated("Cindy", 6, category="girls"))

    filtered = await authed_client.get("/api/workouts", params={"category": "girls", "limit": 2})
    assert filtered.json()["total"] == 1


async def test_list_rejects_an_unknown_mode(authed_client: AsyncClient) -> None:
    """A `mode` filter value outside the enum is rejected."""
    response = await authed_client.get("/api/workouts", params={"mode": "nonsense"})
    assert response.status_code == 422


async def test_categories_lists_this_owners_categories_only(authed_client: AsyncClient) -> None:
    """The categories endpoint returns only this owner's categories: sorted, no blanks, no others'."""
    from shortimer.cache.db import get_workouts_collection

    await _save(authed_client, _dated("Fran", 1, category="benchmark"))
    await _save(authed_client, _dated("Grace", 2, category="girls"))
    await _save(authed_client, _dated("Homemade", 3))  # no category
    await get_workouts_collection().insert_one(
        {
            "_id": "theirs",
            "owner_id": "another-user",
            "name": "Theirs",
            "mode": "for_time",
            "segments": [],
            "category": "hero",
            "created_at": "2026-07-19T00:00:00Z",
            "updated_at": "2026-07-19T00:00:00Z",
        }
    )

    response = await authed_client.get("/api/workouts/categories")
    assert response.status_code == 200
    assert response.json() == ["benchmark", "girls"]  # sorted, no blanks, no "hero"


async def test_categories_path_is_not_read_as_a_workout_id(authed_client: AsyncClient) -> None:
    """`/categories` has to win the match against `/{workout_id}`."""
    response = await authed_client.get("/api/workouts/categories")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# --- Hardening: input limits, id assignment, and upstream error mapping ------


async def test_oversized_paste_is_rejected_before_the_model(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A huge paste must not reach the model and run up a token bill."""

    async def exploding_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that fails the test if it's ever called."""
        raise AssertionError("oversized input must be rejected before parsing")

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", exploding_parse)

    response = await authed_client.post("/api/workouts/parse", json={"text": "x" * 20_001})
    assert response.status_code == 422


async def test_empty_paste_is_rejected(authed_client: AsyncClient) -> None:
    """An empty text field fails validation rather than reaching the parser."""
    assert (await authed_client.post("/api/workouts/parse", json={"text": ""})).status_code == 422


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (
            anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com")),
            504,
        ),
        (
            anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com")
            ),
            503,
        ),
    ],
)
async def test_upstream_failures_map_to_useful_statuses(
    authed_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
    account: User,
    sign_in_as: SignInAs,
) -> None:
    """A parser outage should be a clear, retryable answer — not a bare 500."""

    async def failing_parse(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that raises the parametrized upstream error."""
        raise error

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", failing_parse)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw:
        await sign_in_as(raw, account.id)
        response = await raw.post("/api/workouts/parse", json={"text": "Fran\n21-15-9"})

    assert response.status_code == expected_status
    assert "detail" in response.json()
    # The message is for a human, and says nothing about internals.
    assert "Traceback" not in response.text


async def test_unexpected_errors_do_not_leak_internals(
    monkeypatch: pytest.MonkeyPatch,
    account: User,
    sign_in_as: SignInAs,
) -> None:
    """A genuinely unhandled exception 500s with a generic message, never its own text."""

    async def boom(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that raises an exception carrying text that must never leak."""
        raise RuntimeError("secret internal detail: connection string xyz")

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", boom)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw:
        await sign_in_as(raw, account.id)
        response = await raw.post("/api/workouts/parse", json={"text": "Fran\n21-15-9"})

    assert response.status_code == 500
    assert "secret internal detail" not in response.text
    assert response.json()["detail"] == "Something went wrong. Please try again."


async def test_unexpected_errors_still_carry_cors_headers(
    monkeypatch: pytest.MonkeyPatch,
    account: User,
    sign_in_as: SignInAs,
) -> None:
    """A 500 the browser can't read is a 500 the user never sees.

    The frontend and the API are separate origins in production, so a response
    without `Access-Control-Allow-Origin` is blocked before any code can read
    its body — the friendly message becomes an opaque network error. This is
    what breaks if the catch-all is ever moved back to an `Exception` handler,
    or registered after CORS in `app.py`.
    """

    async def boom(text: str, name_hint: str | None = None, **_: object) -> Workout:
        """A fake parser that always raises, to trigger the catch-all 500 handler."""
        raise RuntimeError("kaboom")

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", boom)

    origin = get_settings().cors_origins[0]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw:
        await sign_in_as(raw, account.id)
        response = await raw.post(
            "/api/workouts/parse",
            json={"text": "Fran\n21-15-9"},
            headers={"Origin": origin},
        )

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == origin


async def test_create_ignores_a_client_supplied_id(authed_client: AsyncClient) -> None:
    """Ids are server-assigned, so a caller can't name another owner's key.

    Letting one through doesn't overwrite anything — the duplicate `_id` fails
    the insert — but the failure tells the caller that id exists, and it is
    reported as a database outage rather than a bad request.
    """
    payload = {
        "workout": {
            "id": "chosen-by-the-client",
            "name": "Squatted",
            "mode": "for_time",
            "segments": [],
        }
    }
    response = await authed_client.post("/api/workouts", json=payload)

    assert response.status_code == 201
    assert response.json()["id"] != "chosen-by-the-client"
    # And the id the caller tried to claim is still free.
    assert (await authed_client.get("/api/workouts/chosen-by-the-client")).status_code == 404
