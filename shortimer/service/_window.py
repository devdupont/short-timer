"""Shared shape behind every "recent days" fetch.

Each daily-feed intake (crossfit.com, Concept2, Wodify) answers the same
question — "the last N days, fetched concurrently, skipping whatever fails or
is missing" — against a different upstream. The window and the fan-out are
identical every time; only `fetch_one` differs.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta

import httpx


def recent_dates(days: int, *, today: date | None = None) -> list[date]:
    """The last `days` calendar dates ending today, newest first."""
    anchor = today or datetime.now().date()
    return [anchor - timedelta(days=offset) for offset in range(max(1, days))]


async def fetch_window[T](
    dates: list[date], fetch_one: Callable[[httpx.AsyncClient, date], Awaitable[T | None]]
) -> list[T]:
    """Fetch every date concurrently over one client, dropping misses."""
    if not dates:
        return []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*(fetch_one(client, day) for day in dates))
    return [item for item in results if item is not None]
