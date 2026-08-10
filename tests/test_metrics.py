"""Event recording, pricing, and the two aggregation endpoints."""

from collections.abc import Generator
from typing import ClassVar, NoReturn

import pytest
from conftest import TEST_EMAIL, TEST_PASSWORD
from httpx import AsyncClient

from shortimer.cache.db import (
    get_events_collection,
    get_users_collection,
    get_workouts_collection,
)
from shortimer.config import get_settings
from shortimer.metrics import (
    MODEL_PRICES,
    estimate_cost,
    model_spend,
    parse_breakdown,
    record,
    record_login,
    record_model_call,
    record_parse,
)
from shortimer.model.metric import EventType, ParseOutcome
from shortimer.model.status import Role
from shortimer.model.user import User
from shortimer.model.workout import Workout, WorkoutMode
from shortimer.users import get_user


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Recording ---------------------------------------------------------------


async def test_an_event_is_stored_with_its_type_and_owner() -> None:
    await record(EventType.LOGIN, owner_id="someone", extra="value")
    [doc] = [d async for d in get_events_collection().find({})]
    assert doc["type"] == "login"
    assert doc["owner_id"] == "someone"
    assert doc["data"]["extra"] == "value"
    assert doc["at"] is not None


async def test_recording_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lost metric must not cost a request the user already paid for."""

    def exploding_collection() -> NoReturn:
        raise RuntimeError("database on fire")

    monkeypatch.setattr("shortimer.metrics.get_events_collection", exploding_collection)
    # No exception, and nothing recorded — the caller carries on regardless.
    await record(EventType.PARSE, outcome="model_call")


async def test_metrics_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off means nothing is written, not just nothing is read."""
    monkeypatch.setenv("METRICS_ENABLED", "false")
    get_settings.cache_clear()
    await record(EventType.LOGIN, owner_id="someone")
    assert await get_events_collection().count_documents({}) == 0


async def test_a_model_call_records_tokens_and_not_dollars() -> None:
    """Prices move; a dollar figure baked into an event would go stale."""
    await record_model_call(
        model="claude-sonnet-5", input_tokens=2850, output_tokens=500, owner_id="me"
    )
    [doc] = [d async for d in get_events_collection().find({})]
    assert doc["data"]["input_tokens"] == 2850
    assert doc["data"]["output_tokens"] == 500
    assert not any("cost" in key for key in doc["data"])


# --- Pricing -----------------------------------------------------------------


def test_a_typical_parse_costs_what_the_pricing_doc_says() -> None:
    """~2,850 in / ~500 out on Sonnet 5 is about 1.6 cents, per docs/pricing.md."""
    cost = estimate_cost("claude-sonnet-5", input_tokens=2850, output_tokens=500)
    assert cost is not None
    assert 0.015 < cost < 0.017


def test_an_unknown_model_reports_no_cost_rather_than_a_wrong_one() -> None:
    assert estimate_cost("some-future-model", input_tokens=1000, output_tokens=100) is None


def test_cached_input_is_much_cheaper_than_fresh_input() -> None:
    """The prompt-caching lever from docs/pricing.md, as arithmetic."""
    fresh = estimate_cost("claude-sonnet-5", input_tokens=2800, output_tokens=0)
    cached = estimate_cost(
        "claude-sonnet-5", input_tokens=0, output_tokens=0, cache_read_tokens=2800
    )
    assert fresh is not None and cached is not None
    assert cached == pytest.approx(fresh * 0.1)


def test_the_configured_model_has_a_price() -> None:
    """A deployment running an unpriced model reports no spend at all."""
    assert get_settings().anthropic_model in MODEL_PRICES


async def test_an_unpriced_model_marks_the_total_incomplete() -> None:
    """A sum that silently drops a model is worse than one that admits a gap."""
    await record_model_call(model="claude-sonnet-5", input_tokens=1000, output_tokens=100)
    await record_model_call(model="mystery-model", input_tokens=1000, output_tokens=100)

    spend = await model_spend(30)
    assert spend["cost_is_complete"] is False
    assert spend["estimated_cost_usd"] > 0
    unpriced = next(row for row in spend["models"] if row["model"] == "mystery-model")
    assert unpriced["estimated_cost_usd"] is None
    assert unpriced["input_tokens"] == 1000


# --- Aggregation -------------------------------------------------------------


async def test_parses_are_grouped_by_outcome() -> None:
    for outcome in (
        ParseOutcome.LIBRARY_HIT,
        ParseOutcome.POOL_HIT,
        ParseOutcome.POOL_HIT,
        ParseOutcome.MODEL_CALL,
    ):
        await record_parse(outcome=outcome, owner_id="me")

    assert await parse_breakdown(30, owner_id="me") == {
        "library_hit": 1,
        "pool_hit": 2,
        "model_call": 1,
    }


async def test_one_users_events_do_not_appear_in_anothers_breakdown() -> None:
    await record_parse(outcome=ParseOutcome.MODEL_CALL, owner_id="me")
    await record_parse(outcome=ParseOutcome.MODEL_CALL, owner_id="someone-else")
    assert await parse_breakdown(30, owner_id="me") == {"model_call": 1}


# --- The endpoints -----------------------------------------------------------


async def test_me_metrics_reports_the_callers_own_usage(
    authed_client: AsyncClient, account: User
) -> None:
    await record_parse(outcome=ParseOutcome.POOL_HIT, owner_id=account.id)
    await record_parse(outcome=ParseOutcome.MODEL_CALL, owner_id=account.id)
    await record_parse(outcome=ParseOutcome.MODEL_CALL, owner_id="someone-else")

    body = (await authed_client.get("/api/metrics/me")).json()
    assert body["parses"]["pool_hits"] == 1
    assert body["parses"]["model_calls"] == 1
    assert body["parses"]["avoided_model_calls"] == 1
    assert body["parses"]["cache_hit_rate"] == 0.5


async def test_me_metrics_never_reports_cost(authed_client: AsyncClient, account: User) -> None:
    """Spend is the operator's business, not every session-holder's."""
    await record_model_call(
        model="claude-sonnet-5", input_tokens=5000, output_tokens=500, owner_id=account.id
    )
    body = (await authed_client.get("/api/metrics/me")).text
    assert "cost" not in body
    assert "token" not in body


async def test_failed_parses_do_not_move_the_cache_hit_rate(
    authed_client: AsyncClient, account: User
) -> None:
    await record_parse(outcome=ParseOutcome.POOL_HIT, owner_id=account.id)
    await record_parse(outcome=ParseOutcome.FAILED, owner_id=account.id)

    parses = (await authed_client.get("/api/metrics/me")).json()["parses"]
    assert parses["failed"] == 1
    # One hit out of one *resolved* parse.
    assert parses["cache_hit_rate"] == 1.0


async def test_metrics_require_a_session(client: AsyncClient) -> None:
    assert (await client.get("/api/metrics/me")).status_code == 401
    assert (await client.get("/api/metrics/operator")).status_code == 401


async def test_operator_metrics_are_off_by_default(authed_client: AsyncClient) -> None:
    """An empty allowlist must mean nobody, not everybody."""
    assert (await authed_client.get("/api/metrics/operator")).status_code == 404


async def test_operator_metrics_open_for_an_allowlisted_user(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, account: User
) -> None:
    monkeypatch.setenv("METRICS_ADMIN_USER_IDS", account.id)
    get_settings.cache_clear()

    await record_model_call(
        model="claude-sonnet-5", input_tokens=2850, output_tokens=500, owner_id="anyone"
    )
    await record_login(owner_id=account.id)

    response = await authed_client.get("/api/metrics/operator")
    assert response.status_code == 200
    body = response.json()
    assert body["spend"]["estimated_cost_usd"] > 0
    assert body["spend"]["cost_is_complete"] is True
    # Two distinct owners: "anyone", who spent, and the caller, who only
    # logged in. Activity of any kind counts, not just spend.
    assert body["active_owners"] == 2


async def test_a_non_allowlisted_user_gets_404_not_403(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Don't confirm the endpoint exists to someone who may not read it."""
    monkeypatch.setenv("METRICS_ADMIN_USER_IDS", "somebody-else")
    get_settings.cache_clear()
    assert (await authed_client.get("/api/metrics/operator")).status_code == 404


async def _set_role(user_id: str, role: str) -> None:
    await get_users_collection().update_one({"_id": user_id}, {"$set": {"role": role}})


async def test_operator_metrics_open_for_an_admin_role(
    authed_client: AsyncClient, account: User
) -> None:
    """The role on the record is the mechanism, with no env var set."""
    await _set_role(account.id, "admin")
    assert (await authed_client.get("/api/metrics/operator")).status_code == 200


async def test_operator_metrics_open_for_staff(authed_client: AsyncClient, account: User) -> None:
    """Support needs the privileged metrics without being the account owner."""
    await _set_role(account.id, "staff")
    assert (await authed_client.get("/api/metrics/operator")).status_code == 200


async def test_plain_user_role_is_refused(authed_client: AsyncClient, account: User) -> None:
    await _set_role(account.id, "user")
    assert (await authed_client.get("/api/metrics/operator")).status_code == 404


async def test_the_seeded_passcode_user_is_not_privileged(
    authed_client: AsyncClient, account: User
) -> None:
    """Everyone shares the passcode account, so it must not read the bill."""
    user = await get_user(account.id)
    assert user is not None
    assert user.role is Role.USER
    assert (await authed_client.get("/api/metrics/operator")).status_code == 404


async def test_a_disabled_account_is_refused_everywhere(
    authed_client: AsyncClient, account: User
) -> None:
    """Status is a separate axis from role: an admin who is disabled is out."""
    await get_users_collection().update_one(
        {"_id": account.id}, {"$set": {"role": "admin", "status": "disabled"}}
    )
    assert (await authed_client.get("/api/metrics/operator")).status_code == 403


async def test_the_window_is_bounded(authed_client: AsyncClient) -> None:
    """Past retention the answer is silently partial, so refuse it."""
    assert (await authed_client.get("/api/metrics/me?days=4000")).status_code == 422


# --- Instrumentation, end to end ---------------------------------------------


async def test_starting_a_workout_is_recorded(authed_client: AsyncClient, account: User) -> None:
    workout = Workout(name="Fran", mode=WorkoutMode.FOR_TIME)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = account.id
    await get_workouts_collection().insert_one(doc)

    response = await authed_client.post(f"/api/workouts/{workout.id}/started")
    assert response.status_code == 204

    body = (await authed_client.get("/api/metrics/me")).json()
    assert body["workouts_started"] == 1


async def test_starting_another_owners_workout_is_a_404(authed_client: AsyncClient) -> None:
    """Telemetry must not become an existence oracle for other people's rows."""
    workout = Workout(name="Theirs", mode=WorkoutMode.FOR_TIME)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = "someone-else"
    await get_workouts_collection().insert_one(doc)

    assert (await authed_client.post(f"/api/workouts/{workout.id}/started")).status_code == 404
    assert await get_events_collection().count_documents({"type": "workout_started"}) == 0


async def test_logging_in_is_recorded(client: AsyncClient, account: User) -> None:
    response = await client.post(
        "/api/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 204
    assert await get_events_collection().count_documents({"type": "login"}) == 1


async def test_a_rejected_login_is_not_recorded(client: AsyncClient, account: User) -> None:
    await client.post("/api/auth/login", json={"email": TEST_EMAIL, "password": "wrong"})
    assert await get_events_collection().count_documents({"type": "login"}) == 0


async def test_a_parse_records_both_the_outcome_and_the_tokens(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, account: User
) -> None:
    """The two halves of the cost picture: what was asked for, and what it cost."""

    class FakeUsage:
        input_tokens = 2850
        output_tokens = 500
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    class FakeBlock:
        type = "tool_use"
        input: ClassVar[dict[str, object]] = {
            "name": "Fran",
            "mode": "for_time",
            "segments": [],
        }

    class FakeResponse:
        model = "claude-sonnet-5"
        usage = FakeUsage()
        content: ClassVar[list[object]] = [FakeBlock()]

    class FakeMessages:
        async def create(self, **_: object) -> FakeResponse:
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr("shortimer.service.llm._client", lambda: FakeClient())

    response = await authed_client.post("/api/workouts/parse", json={"text": "Fran 21-15-9"})
    assert response.status_code == 200

    events = get_events_collection()
    parse = await events.find_one({"type": "parse"})
    call = await events.find_one({"type": "model_call"})
    assert parse is not None and parse["data"]["outcome"] == "model_call"
    assert call is not None
    assert call["data"]["input_tokens"] == 2850
    assert call["data"]["purpose"] == "parse"
    assert call["owner_id"] == account.id


async def test_a_cached_parse_costs_nothing_and_says_so(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parse pool's whole value proposition, as a measurable event."""
    from shortimer.cache.parse import remember_parse

    text = "21-15-9 Thrusters and Pull-ups"
    await remember_parse(Workout(name="Fran", mode=WorkoutMode.FOR_TIME, source_text=text))

    async def exploding_parse(*_: object, **__: object) -> Workout:
        raise AssertionError("the pool should have served this")

    monkeypatch.setattr("shortimer.router.workouts.parse_workout_text", exploding_parse)

    assert (await authed_client.post("/api/workouts/parse", json={"text": text})).status_code == 200

    events = get_events_collection()
    parse = await events.find_one({"type": "parse"})
    assert parse is not None and parse["data"]["outcome"] == "pool_hit"
    assert await events.count_documents({"type": "model_call"}) == 0


async def test_completing_a_workout_is_recorded(authed_client: AsyncClient, account: User) -> None:
    workout = Workout(name="Fran", mode=WorkoutMode.FOR_TIME)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = account.id
    await get_workouts_collection().insert_one(doc)

    await authed_client.post(f"/api/workouts/{workout.id}/started")
    response = await authed_client.post(
        f"/api/workouts/{workout.id}/completed", json={"elapsed_seconds": 254.7}
    )
    assert response.status_code == 204

    body = (await authed_client.get("/api/metrics/me")).json()
    assert body["workouts_started"] == 1
    assert body["workouts_completed"] == 1
    assert body["completion_rate"] == 1.0

    event = await get_events_collection().find_one({"type": "workout_completed"})
    assert event is not None
    assert event["data"]["elapsed_seconds"] == 254.7
    assert event["data"]["mode"] == "for_time"


async def test_abandoned_workouts_show_up_as_a_lower_completion_rate(
    authed_client: AsyncClient, account: User
) -> None:
    """The number a start count alone can't give you."""
    workout = Workout(name="Murph", mode=WorkoutMode.FOR_TIME)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = account.id
    await get_workouts_collection().insert_one(doc)

    for _ in range(4):
        await authed_client.post(f"/api/workouts/{workout.id}/started")
    await authed_client.post(f"/api/workouts/{workout.id}/completed", json={"elapsed_seconds": 60})

    body = (await authed_client.get("/api/metrics/me")).json()
    assert body["completion_rate"] == 0.25


async def test_a_completion_rate_cannot_exceed_one(
    authed_client: AsyncClient, account: User
) -> None:
    """A workout started before the window and finished inside it would."""
    workout = Workout(name="Cindy", mode=WorkoutMode.AMRAP)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = account.id
    await get_workouts_collection().insert_one(doc)

    await authed_client.post(f"/api/workouts/{workout.id}/started")
    for _ in range(3):
        await authed_client.post(
            f"/api/workouts/{workout.id}/completed", json={"elapsed_seconds": 1200}
        )

    assert (await authed_client.get("/api/metrics/me")).json()["completion_rate"] == 1.0


async def test_an_implausible_elapsed_time_is_rejected(
    authed_client: AsyncClient, account: User
) -> None:
    """A tab left open overnight would drag every average it touches."""
    workout = Workout(name="Fran", mode=WorkoutMode.FOR_TIME)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = account.id
    await get_workouts_collection().insert_one(doc)

    response = await authed_client.post(
        f"/api/workouts/{workout.id}/completed", json={"elapsed_seconds": 60 * 60 * 24}
    )
    assert response.status_code == 422
    assert await get_events_collection().count_documents({"type": "workout_completed"}) == 0


async def test_completing_another_owners_workout_is_a_404(authed_client: AsyncClient) -> None:
    workout = Workout(name="Theirs", mode=WorkoutMode.FOR_TIME)
    doc = workout.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    doc["owner_id"] = "someone-else"
    await get_workouts_collection().insert_one(doc)

    response = await authed_client.post(
        f"/api/workouts/{workout.id}/completed", json={"elapsed_seconds": 100}
    )
    assert response.status_code == 404
    assert await get_events_collection().count_documents({"type": "workout_completed"}) == 0
