import anthropic
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from short_timer.app import app
from short_timer.config import get_settings
from short_timer.models import Workout, WorkoutMode
from short_timer.ratelimit import RateLimit, enforce


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


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Settings are cached; drop it so per-test env tweaks take effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_enforce_allows_up_to_the_limit_then_blocks() -> None:
    limit = RateLimit("test-scope", limit=3, window_seconds=3600)
    for _ in range(3):
        await enforce(limit, "subject-a")

    with pytest.raises(Exception) as excinfo:
        await enforce(limit, "subject-a")
    assert getattr(excinfo.value, "status_code", None) == 429
    # Clients are told when to come back.
    assert "Retry-After" in getattr(excinfo.value, "headers", {})


async def test_limits_are_tracked_per_subject() -> None:
    limit = RateLimit("test-scope", limit=1, window_seconds=3600)
    await enforce(limit, "subject-a")
    # A different subject has its own budget.
    await enforce(limit, "subject-b")


async def test_repeated_bad_passcodes_are_throttled(client: AsyncClient) -> None:
    """The shared passcode is guessable without a cap on attempts."""
    attempts = get_settings().login_attempts_per_15_min
    statuses = [
        (await client.post("/api/auth/login", json={"passcode": "wrong"})).status_code
        for _ in range(attempts + 2)
    ]
    assert statuses[0] == 401  # rejected on merit
    assert statuses[-1] == 429  # eventually refused outright


async def test_successful_logins_do_not_consume_the_attempt_budget(
    client: AsyncClient,
) -> None:
    """Everyone at a gym shares one WiFi IP; they mustn't lock each other out."""
    attempts = get_settings().login_attempts_per_15_min
    for _ in range(attempts * 3):
        response = await client.post("/api/auth/login", json={"passcode": "test-passcode"})
        assert response.status_code == 204


async def test_a_guesser_is_locked_out_without_blocking_the_real_passcode(
    client: AsyncClient,
) -> None:
    attempts = get_settings().login_attempts_per_15_min
    for _ in range(attempts):
        await client.post("/api/auth/login", json={"passcode": "wrong"})

    # Further guesses are refused outright.
    blocked = await client.post("/api/auth/login", json={"passcode": "wrong"})
    assert blocked.status_code == 429


async def test_oversized_paste_is_rejected_before_the_model(
    authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A huge paste must not reach the model and run up a token bill."""

    async def exploding_parse(text: str, name_hint: str | None = None) -> Workout:
        raise AssertionError("oversized input must be rejected before parsing")

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", exploding_parse)

    response = await authed_client.post("/api/workouts/parse", json={"text": "x" * 20_001})
    assert response.status_code == 422


async def test_empty_paste_is_rejected(authed_client: AsyncClient) -> None:
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
) -> None:
    """A parser outage should be a clear, retryable answer — not a bare 500."""

    async def failing_parse(text: str, name_hint: str | None = None) -> Workout:
        raise error

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", failing_parse)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw:
        await raw.post("/api/auth/login", json={"passcode": "test-passcode"})
        response = await raw.post("/api/workouts/parse", json={"text": "Fran\n21-15-9"})

    assert response.status_code == expected_status
    assert "detail" in response.json()
    # The message is for a human, and says nothing about internals.
    assert "Traceback" not in response.text


async def test_unexpected_errors_do_not_leak_internals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(text: str, name_hint: str | None = None) -> Workout:
        raise RuntimeError("secret internal detail: connection string xyz")

    monkeypatch.setattr("short_timer.routers.workouts.parse_workout_text", boom)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw:
        await raw.post("/api/auth/login", json={"passcode": "test-passcode"})
        response = await raw.post("/api/workouts/parse", json={"text": "Fran\n21-15-9"})

    assert response.status_code == 500
    assert "secret internal detail" not in response.text
    assert response.json()["detail"] == "Something went wrong. Please try again."


async def test_readiness_reports_database_health(client: AsyncClient) -> None:
    response = await client.get("/api/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


async def test_seeded_workout_model_still_round_trips() -> None:
    """Guard the tightened request model against over-restricting real data."""
    workout = Workout(name="Fran", mode=WorkoutMode.FOR_TIME, source_text="21-15-9")
    assert workout.source_text == "21-15-9"
