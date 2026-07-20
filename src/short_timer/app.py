import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from short_timer.config import get_settings
from short_timer.db import (
    backfill_owner_ids,
    backfill_source_hashes,
    ensure_indexes,
    get_database,
)
from short_timer.errors import register_error_handlers
from short_timer.routers import auth, wods, workouts
from short_timer.parse_cache import (
    backfill_parse_sources,
    migrate_wod_parses,
    prune_expired_parses,
)
from short_timer.wod_cache import (
    REFRESH_INTERVAL_SECONDS,
    ensure_wods_parsed,
    refresh_wod_cache,
)

logger = logging.getLogger(__name__)


async def _refresh_wods_daily() -> None:
    """Keep the WOD cache warm so no request ever waits on crossfit.com."""
    while True:
        try:
            await refresh_wod_cache()
            # Parse each day once here rather than per user on first load.
            # Runs even when the fetch was skipped as fresh, so a day that
            # failed to parse earlier gets another attempt.
            await ensure_wods_parsed()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let a bad fetch kill the loop
            logger.exception("WOD cache refresh failed; will retry tomorrow.")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


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
        except Exception:  # noqa: BLE001 - a failed sweep shouldn't kill the loop
            logger.exception("Parse pool sweep failed; will retry next cycle.")
        await asyncio.sleep(_PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Best-effort: a database that's slow or unreachable at boot shouldn't
    # stop the app from serving.
    try:
        await ensure_indexes()
        hashes = await backfill_source_hashes()
        owners = await backfill_owner_ids()
        moved = await migrate_wod_parses()
        labelled = await backfill_parse_sources()
        if hashes or owners or moved or labelled:
            logger.info(
                "Backfilled source_hash on %d workout(s), owner_id on %d, moved %d "
                "WOD parse(s) into the shared pool, labelled %d for retention.",
                hashes,
                owners,
                moved,
                labelled,
            )
    except Exception:  # noqa: BLE001 - startup maintenance is non-critical
        logger.exception("Skipped startup database maintenance.")

    background = [
        asyncio.create_task(_refresh_wods_daily()),
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(auth.router)
app.include_router(workouts.router)
app.include_router(wods.router)


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
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        logger.exception("Readiness check failed.")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "database": "unreachable"},
        )
    return JSONResponse(content={"status": "ok", "database": "ok"})


def run() -> None:
    import uvicorn

    uvicorn.run("short_timer.app:app", host="0.0.0.0", port=8000, reload=True)
