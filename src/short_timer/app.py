import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from short_timer.config import get_settings
from short_timer.db import backfill_owner_ids, backfill_source_hashes, ensure_indexes
from short_timer.routers import auth, wods, workouts
from short_timer.parse_cache import migrate_wod_parses
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Best-effort: a database that's slow or unreachable at boot shouldn't
    # stop the app from serving.
    try:
        await ensure_indexes()
        hashes = await backfill_source_hashes()
        owners = await backfill_owner_ids()
        moved = await migrate_wod_parses()
        if hashes or owners or moved:
            logger.info(
                "Backfilled source_hash on %d workout(s), owner_id on %d, "
                "and moved %d WOD parse(s) into the shared pool.",
                hashes,
                owners,
                moved,
            )
    except Exception:  # noqa: BLE001 - startup maintenance is non-critical
        logger.exception("Skipped startup database maintenance.")

    refresher = asyncio.create_task(_refresh_wods_daily())
    try:
        yield
    finally:
        refresher.cancel()
        with suppress(asyncio.CancelledError):
            await refresher


app = FastAPI(title="short-timer", version="0.1.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(workouts.router)
app.include_router(wods.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    uvicorn.run("short_timer.app:app", host="0.0.0.0", port=8000, reload=True)
