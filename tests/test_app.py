"""App-level wiring: health/readiness reporting, and the background refresh loops."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import pytest
from httpx import AsyncClient

from shortimer import app as app_module

#: A stand-in fetch, told which pass it's on so it can fail selectively.
Fetch = Callable[[int], Awaitable[int]]
Followup = Callable[[], Awaitable[int]]


async def test_readiness_reports_database_health(client: AsyncClient) -> None:
    """`/api/ready` reports the database as reachable when it is."""
    response = await client.get("/api/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


class _Recorder:
    """Stands in for `record_feed_refresh`, capturing how each pass was reported."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


async def _run_loop_until(
    monkeypatch: pytest.MonkeyPatch, fetch: Fetch, *followups: Followup, passes: int
) -> _Recorder:
    """Run `_refresh_loop` for `passes` iterations, then cancel it.

    The interval is zero and the loop is driven by an event rather than by
    sleeping, so this neither waits on wall-clock time nor spins: `fetch`
    parks once it has been called often enough, and the cancel unwinds it.
    """
    recorder = _Recorder()
    monkeypatch.setattr(app_module, "record_feed_refresh", recorder)

    reached = asyncio.Event()
    calls = 0

    async def counting_fetch() -> int:
        nonlocal calls
        calls += 1
        if calls >= passes:
            reached.set()
            # Park, so the loop stops churning while the test makes assertions.
            await asyncio.sleep(3600)
        return await fetch(calls)

    task = asyncio.create_task(app_module._refresh_loop("crossfit", 0, counting_fetch, *followups))
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return recorder


async def test_a_failing_fetch_does_not_kill_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract the loop exists for: one bad day must not stop the feed forever.

    Without this the first upstream blip would silently end refreshes until
    someone restarted the process, and the home page would quietly go stale.
    """

    async def fetch(call: int) -> int:
        if call == 1:
            raise RuntimeError("upstream is down")
        return 7

    recorder = await _run_loop_until(monkeypatch, fetch, passes=3)

    assert recorder.calls[0] == {"feed": "crossfit", "ok": False}
    assert recorder.calls[1] == {"feed": "crossfit", "ok": True, "rows": 7}


async def test_a_successful_pass_reports_the_rows_it_wrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row count reported is the fetch's own, which is what "days cached" means."""

    async def fetch(_call: int) -> int:
        return 3

    recorder = await _run_loop_until(monkeypatch, fetch, passes=2)

    assert recorder.calls[0] == {"feed": "crossfit", "ok": True, "rows": 3}


async def test_followups_run_after_a_successful_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-parsing runs in the same pass, so a day is cached and parsed together."""
    ran: list[str] = []

    async def fetch(_call: int) -> int:
        ran.append("fetch")
        return 1

    async def parse() -> int:
        ran.append("parse")
        # Deliberately a different number: a followup's return value is ignored,
        # because "workouts parsed on this pass" is zero on any cycle that found
        # nothing new and would read as a failing feed.
        return 99

    recorder = await _run_loop_until(monkeypatch, fetch, parse, passes=2)

    assert ran[:2] == ["fetch", "parse"]
    assert recorder.calls[0] == {"feed": "crossfit", "ok": True, "rows": 1}


async def test_a_failing_followup_is_reported_as_a_failed_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetch that worked but a parse that didn't is not a successful refresh."""

    async def fetch(_call: int) -> int:
        return 1

    async def failing_parse() -> int:
        raise RuntimeError("parser is down")

    recorder = await _run_loop_until(monkeypatch, fetch, failing_parse, passes=3)

    assert recorder.calls[0] == {"feed": "crossfit", "ok": False}


async def test_cancelling_the_loop_actually_stops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown has to end these, so `CancelledError` must not be swallowed as a failure.

    The loop catches `Exception` broadly to survive bad fetches; `CancelledError`
    is re-raised ahead of that, and it derives from `BaseException` rather than
    `Exception` precisely so this stays true.
    """
    recorder = _Recorder()
    monkeypatch.setattr(app_module, "record_feed_refresh", recorder)

    started = asyncio.Event()

    async def fetch() -> int:
        started.set()
        await asyncio.sleep(3600)
        return 0

    task = asyncio.create_task(app_module._refresh_loop("crossfit", 0, fetch))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    # A cancellation is not a failed refresh, and must not be recorded as one.
    assert recorder.calls == []


async def test_the_parse_sweep_survives_a_failing_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same contract as the feed loops: a failed sweep retries rather than ending."""
    calls = 0
    reached = asyncio.Event()

    async def prune() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("mongo went away")
        reached.set()
        await asyncio.sleep(3600)
        return 0

    monkeypatch.setattr(app_module, "prune_expired_parses", prune)
    monkeypatch.setattr(app_module, "_PRUNE_INTERVAL_SECONDS", 0)

    task = asyncio.create_task(app_module._prune_parses_monthly())
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert calls >= 2


# --- Liveness, readiness, and startup ----------------------------------------


async def test_health_is_liveness_only(client: AsyncClient) -> None:
    """`/api/health` answers for the process, not its dependencies.

    It stays 200 while the database is unreachable on purpose: a platform
    restarts the container on a failed *liveness* check, and restarting won't
    bring Mongo back.
    """
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_an_unreachable_database(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database that can't be pinged takes the instance out of rotation, not down."""

    def unreachable() -> Any:
        raise RuntimeError("no primary available")

    monkeypatch.setattr(app_module, "get_database", unreachable)

    response = await client.get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unreachable"}


async def _stub_loops(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    """Replace the background loops with ones that park and report cancellation.

    The real loops fetch from the network and can spend Anthropic calls on
    their pre-parse followups, neither of which belongs in a test.
    """
    started: list[str] = []
    cancelled: list[str] = []

    async def park(name: str) -> None:
        started.append(name)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    async def fake_loop(feed: str, _interval: int, *_rest: object) -> None:
        await park(feed)

    async def fake_prune() -> None:
        await park("prune")

    monkeypatch.setattr(app_module, "_refresh_loop", fake_loop)
    monkeypatch.setattr(app_module, "_prune_parses_monthly", fake_prune)
    return started, cancelled


_EXPECTED_LOOPS = {"crossfit", "concept2", "hybrid", "gym", "prune"}


async def test_lifespan_starts_every_loop_and_stops_them_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each feed gets a loop, and shutdown ends all of them.

    A loop left running past shutdown keeps a cancelled event loop's tasks
    alive and turns a clean exit into a hang.
    """
    started, cancelled = await _stub_loops(monkeypatch)

    async with app_module.lifespan(app_module.app):
        for _ in range(20):
            if set(started) == _EXPECTED_LOOPS:
                break
            await asyncio.sleep(0)
        assert set(started) == _EXPECTED_LOOPS
        assert cancelled == []

    assert set(cancelled) == _EXPECTED_LOOPS


async def test_a_database_down_at_boot_does_not_stop_the_app_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup maintenance is best-effort, deliberately.

    Refusing to boot without Mongo would mean a brief outage during a deploy
    left nothing running at all — including `/api/health`, which is what tells
    the platform whether to keep trying.
    """
    await _stub_loops(monkeypatch)

    async def unreachable() -> None:
        raise RuntimeError("no primary available")

    monkeypatch.setattr(app_module, "init_documents", unreachable)

    # The assertion is that this block is reached and left without raising.
    async with app_module.lifespan(app_module.app):
        pass
