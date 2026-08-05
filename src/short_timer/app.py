import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from short_timer import concept2_cache, hybrid_cache
from short_timer.config import get_settings
from short_timer.db import ensure_indexes, get_database
from short_timer.errors import register_error_handlers
from short_timer.gym_cache import (
    REFRESH_INTERVAL_SECONDS as GYM_REFRESH_INTERVAL_SECONDS,
)
from short_timer.gym_cache import refresh_all_configured
from short_timer.metrics import record_feed_refresh
from short_timer.parse_cache import prune_expired_parses
from short_timer.routers import (
    admin,
    auth,
    concept2,
    gym,
    hybrid,
    me,
    metrics,
    wods,
    workouts,
)
from short_timer.wod_cache import (
    REFRESH_INTERVAL_SECONDS,
    ensure_wods_parsed,
    refresh_wod_cache,
)

logger = logging.getLogger(__name__)


async def _refresh_loop(
    feed: str,
    interval_seconds: int,
    fetch: Callable[[], Awaitable[int]],
    *followups: Callable[[], Awaitable[int]],
) -> None:
    """Keep one source warm forever, recording how each attempt went.

    Every feed wants the same three things — fetch, pre-parse, never die — and
    written out four times that was four places to remember to instrument. The
    per-feed reasoning that used to live in four docstrings now sits at the
    call sites in `lifespan`, where the intervals are chosen.

    `followups` run after a successful fetch and their return values are
    ignored: they pre-parse, and "days cached" is the number worth reporting,
    not "workouts parsed on this particular pass" — which is zero on any cycle
    that found nothing new, and would read as a failing feed.
    """
    while True:
        try:
            rows = await fetch()
            for followup in followups:
                await followup()
        except asyncio.CancelledError:
            raise
        except Exception:  # never let a bad fetch kill the loop
            logger.exception("%s refresh failed; will retry next cycle.", feed)
            await record_feed_refresh(feed=feed, ok=False)
        else:
            await record_feed_refresh(feed=feed, ok=True, rows=rows)
        await asyncio.sleep(interval_seconds)


#: User-submitted parses age out; sweep for them monthly. The loop runs once
#: on startup too, so a host that restarts often still gets swept.
_PRUNE_INTERVAL_SECONDS = 30 * 24 * 60 * 60


async def _prune_parses_monthly() -> None:
    """Drop user-submitted parses past their retention window."""
    while True:
        try:
            await prune_expired_parses()
        except asyncio.CancelledError:
            raise
        except Exception:  # a failed sweep shouldn't kill the loop
            logger.exception("Parse pool sweep failed; will retry next cycle.")
        await asyncio.sleep(_PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Best-effort: a database that's slow or unreachable at boot shouldn't
    # stop the app from serving.
    #
    # The pre-accounts backfills that used to run here are gone. They existed
    # to repair rows written before `source_hash`, `owner_id` and parse
    # provenance existed, and no such row can exist any more: accounts landed
    # on an empty database, so every document has been written by code that
    # sets all three.
    try:
        await ensure_indexes()
    except Exception:  # startup maintenance is non-critical
        logger.exception("Skipped startup database maintenance.")

    background = [
        # Pre-parsing each day here rather than per user on first load is what
        # makes one model call serve every reader. It runs even when the fetch
        # was skipped as fresh, so a day that failed to parse gets another go.
        asyncio.create_task(
            _refresh_loop(
                "crossfit", REFRESH_INTERVAL_SECONDS, refresh_wod_cache, ensure_wods_parsed
            )
        ),
        asyncio.create_task(
            _refresh_loop(
                "concept2",
                concept2_cache.REFRESH_INTERVAL_SECONDS,
                concept2_cache.refresh_concept2_cache,
                concept2_cache.ensure_wods_parsed,
            )
        ),
        # Checked daily but only re-fetched weekly (see its MIN_REFRESH_INTERVAL)
        # — the routine is a fixed rotation, so most days there's nothing new.
        asyncio.create_task(
            _refresh_loop(
                "hybrid",
                hybrid_cache.REFRESH_INTERVAL_SECONDS,
                hybrid_cache.refresh_hybrid_cache,
                hybrid_cache.ensure_wods_parsed,
            )
        ),
        # More often than the crossfit.com refresh: a gym may post the day's
        # workout at any hour, and there's no single publish time to anchor to.
        # It pre-parses internally, per gym, so it takes no followup.
        asyncio.create_task(
            _refresh_loop("gym", GYM_REFRESH_INTERVAL_SECONDS, refresh_all_configured)
        ),
        asyncio.create_task(_prune_parses_monthly()),
    ]
    try:
        yield
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="short-timer", version="0.1.0", lifespan=lifespan)

settings = get_settings()

# Before CORS, deliberately: `add_middleware` puts the newest layer outermost,
# so registering the error handlers first is what leaves CORS wrapping them.
# Reverse these two and an unhandled 500 goes out with no CORS headers, which
# a browser reports as a CORS failure rather than showing our message.
register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(workouts.router)
app.include_router(wods.router)
app.include_router(concept2.router)
app.include_router(hybrid.router)
app.include_router(gym.router)
app.include_router(metrics.router)
app.include_router(admin.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness only — the process is up and serving."""
    return {"status": "ok"}


@app.get("/api/ready")
async def ready() -> JSONResponse:
    """Readiness — checks the dependency the app can't serve without.

    Separate from /health so a platform restarts the container on a genuine
    hang, but merely stops routing traffic when the database is briefly
    unreachable.
    """
    try:
        await get_database().command("ping")
    except Exception:  # any failure means "not ready"
        logger.exception("Readiness check failed.")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "database": "unreachable"},
        )
    return JSONResponse(content={"status": "ok", "database": "ok"})


def run() -> None:
    import uvicorn

    uvicorn.run("short_timer.app:app", host="0.0.0.0", port=8000, reload=True)
