"""Fetch the daily erg Workout of the Day from Concept2.

Concept2 programs one workout a day for the RowErg, SkiErg and BikeErg. It's
the *same* workout on all three — where a machine needs a different number,
that's written into the description ("BikeErg: 1000m") rather than published as
a separate workout — so we fetch one erg and serve it for all of them.

``concept2.com/training/wod`` renders the workout client-side and ships none of
it in the HTML. The Honorboard page for a given day is server-rendered and
carries the workout in a fixed heading, which is what we read:

    <h2> « July 22, 2026 </h2>
    <h3>2/3/2/3/2/3/2 minutes with 1 minute rest</h3>
    <p><strong>Seven alternating two and three minute intervals. …</strong></p>

Both halves are kept. The `h3` is the workout as Concept2 names it, and the
paragraph is what tells the parser that "2/3/2/3/2/3/2" means seven intervals
rather than one long piece — without it the heading alone is ambiguous.

Compared with crossfit.com this source is easy: every day is interval work, so
there are no rest days to detect and no scaling prose to strip. Days outside
the published range return a 500 rather than a 404, so any non-200 is simply
treated as "no workout that day".
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

logger = logging.getLogger(__name__)

USER_AGENT = "short-timer/0.1 (+https://github.com/devdupont/short-timer)"
#: The workout is identical across ergs, so one fetch covers all three. This is
#: only which Honorboard we read it from.
_ERG = "rowerg"
_URL = "https://log.concept2.com/wod/{day:%Y-%m-%d}/" + _ERG
_REQUEST_TIMEOUT = 15.0


class Concept2Wod(BaseModel):
    """A single day's erg Workout of the Day.

    Same shape as `crossfit.Wod`, kept separate for the same reason `GymWod`
    is: the sources have different lifecycles, and collapsing them into one
    model would invite assumptions from one to leak into the other.
    """

    date: date
    title: str
    text: str
    url: str


def workout_url(day: date) -> str:
    return _URL.format(day=day)


def parse_wod_page(html: str, day: date) -> Concept2Wod | None:
    """Pull the workout out of a Honorboard page, or None if it isn't there.

    Scoped to the `content` section because the rest of the page is a country
    filter and a few hundred rows of leaderboard, and we want a miss here to
    read as "no workout" rather than as some stray heading from the table.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("section", class_="content")
    if content is None:
        return None

    heading = content.find("h3")
    if heading is None:
        return None
    title = heading.get_text(" ", strip=True)
    if not title:
        return None

    # The description is the paragraph immediately after the heading. It's
    # optional in the sense that we still have a usable workout without it —
    # just a harder one to parse.
    description = ""
    sibling = heading.find_next_sibling()
    if sibling is not None and sibling.name == "p":
        description = sibling.get_text(" ", strip=True)

    text = f"{title}\n\n{description}".strip() if description else title
    return Concept2Wod(date=day, title=title, text=text, url=workout_url(day))


async def fetch_wod(client: httpx.AsyncClient, day: date) -> Concept2Wod | None:
    """Fetch one day's workout, or None if it's missing or the request fails."""
    url = workout_url(day)
    try:
        response = await client.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        # Dates before Concept2 started publishing, and any date in the future,
        # answer 500 — so this is an ordinary miss, not something to shout about.
        logger.debug("No Concept2 WOD for %s (HTTP %d).", day, response.status_code)
        return None

    return parse_wod_page(response.text, day)


async def fetch_days(days: list[date]) -> list[Concept2Wod]:
    """Fetch a specific set of days concurrently, newest first, skipping misses."""
    if not days:
        return []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*(fetch_wod(client, day) for day in days))
    found = [wod for wod in results if wod is not None]
    return sorted(found, key=lambda wod: wod.date, reverse=True)


async def fetch_recent_wods(days: int = 7, *, today: date | None = None) -> list[Concept2Wod]:
    """Fetch the most recent `days` workouts (today first), skipping any that fail."""
    anchor = today or datetime.now().date()
    return await fetch_days([anchor - timedelta(days=offset) for offset in range(max(1, days))])
