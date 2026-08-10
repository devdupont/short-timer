"""Fetch daily Workout of the Day content from crossfit.com.

crossfit.com serves each day's workout as JSON at
``https://www.crossfit.com/workout/YYYY/MM/DD`` — the same endpoint its own
single-page app calls. We read ``wods.wodRaw`` (the raw workout text) and
``wods.title``. The site only publishes today and past days (never future
ones), and occasionally 502s or returns an empty body for a given day, so
missing/failed days are simply skipped.

This runs server-side: the browser can't call crossfit.com cross-origin, and
it lets us reuse the source text for LLM-parse caching.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta

import httpx
from pydantic import BaseModel

# Leading \W* skips the markdown bold markers crossfit.com wraps it in.
_REST_DAY = re.compile(r"^\W*rest day", re.IGNORECASE)


def is_rest_day(text: str) -> bool:
    """crossfit.com programs scheduled rest days, which have no workout to time."""
    return bool(_REST_DAY.match(text.strip()))


USER_AGENT = "shortimer/0.1 (+https://github.com/devdupont/shortimer)"
_API_URL = "https://www.crossfit.com/workout/{year}/{month:02d}/{day:02d}"
_REQUEST_TIMEOUT = 15.0


class Wod(BaseModel):
    """A single day's Workout of the Day from crossfit.com."""

    date: date
    title: str
    text: str
    url: str


def _public_url(day: date) -> str:
    return f"https://www.crossfit.com/{day:%y%m%d}"


async def fetch_wod(client: httpx.AsyncClient, day: date) -> Wod | None:
    """Fetch one day's WOD, or None if it's missing or the request fails."""
    url = _API_URL.format(year=day.year, month=day.month, day=day.day)
    try:
        response = await client.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200 or "json" not in response.headers.get("content-type", ""):
        return None
    try:
        payload = response.json()
    except ValueError:
        return None

    wod = payload.get("wods") or {}
    text = (wod.get("wodRaw") or "").strip()
    if not text:
        return None

    title = (wod.get("title") or "").strip() or day.strftime("%A %y%m%d")
    return Wod(date=day, title=title, text=text, url=_public_url(day))


async def fetch_recent_wods(days: int = 7, *, today: date | None = None) -> list[Wod]:
    """Fetch the most recent `days` WODs (today first), skipping any that fail."""
    anchor = today or datetime.now().date()
    targets = [anchor - timedelta(days=offset) for offset in range(max(1, days))]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*(fetch_wod(client, day) for day in targets))
    return [wod for wod in results if wod is not None]
