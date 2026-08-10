"""Fetch the Hybrid Calisthenics routine — a weekly rotation, not a daily post.

This source works differently from the two before it. crossfit.com and Concept2
publish a *new* workout each day at a dated URL; Hybrid Calisthenics publishes
one page describing a fixed six-day rotation plus a rest day, and "today's
workout" is whichever day of the week it currently is. So we fetch the rotation
once, cache it as a single document, and project it onto dates on the way out.

The page is Squarespace, and each day is its own content block:

    <h1>Monday</h1>
    <p class="sqsrte-large">Pushups (2-3 Sets)</p>
    <p><strong>Variations:</strong> <a>Wall Pushups</a> …</p>
    <p class="sqsrte-large">Leg Raises (2-3 Sets)</p>

We keep the `sqsrte-large` lines, which are the workout, and drop the variation
ladders. Those matter enormously to the athlete — they're how you pick your
level — but they're reference material rather than the session, and pulling ten
progressions per exercise into the workout text would bury the two lines that
actually say what to do. The card links back to the site for them.

Nothing here is timed. Every set is "as many as you can, stopping 1-2 reps
before failure", which is a real workout with no clock to run — see
`WorkoutMode.CUSTOM` and the untimed branch of the timer view.
"""

import logging
import re
from datetime import date

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from shortimer.model.feed_item import DatedFeedItem

logger = logging.getLogger(__name__)

USER_AGENT = "shortimer/0.1 (+https://github.com/devdupont/shortimer)"
PAGE_URL = "https://www.hybridcalisthenics.com/wotd"
_REQUEST_TIMEOUT = 15.0

#: Indexed to match `date.weekday()` — Monday is 0.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: The rest day announces itself in the same slot an exercise would occupy.
_REST_DAY = re.compile(r"^\W*(a day of rest|rest day)", re.IGNORECASE)

#: "Pushups (2-3 Sets)" -> "Pushups", for building a title that reads as a
#: workout name rather than a set count.
_SET_SUFFIX = re.compile(r"\s*\(\d+(?:\s*-\s*\d+)?\s*sets?\)\s*$", re.IGNORECASE)


def is_rest_day(text: str) -> bool:
    """Hybrid Calisthenics programs Sunday off, which has nothing to load."""
    return bool(_REST_DAY.match(text.strip()))


class HybridWorkout(DatedFeedItem):
    """One day of the rotation, projected onto a real date."""


class Rotation(BaseModel):
    """The whole week, keyed by weekday name.

    Stored and cached as one unit because that's how it's published — there's
    no such thing as fetching a single day from this source.
    """

    days: dict[str, list[str]]

    def for_date(self, day: date) -> HybridWorkout | None:
        """The workout falling on `day`, or None if the rotation lacks it."""
        lines = self.days.get(WEEKDAYS[day.weekday()])
        if not lines:
            return None
        return HybridWorkout(date=day, title=_title_for(lines), text="\n".join(lines), url=PAGE_URL)


def _title_for(lines: list[str]) -> str:
    """A short name for the day: "Pushups & Leg Raises", or "A Day of Rest"."""
    names = [_SET_SUFFIX.sub("", line).strip() for line in lines]
    return " & ".join(name for name in names if name) or lines[0]


def parse_rotation(html: str) -> Rotation:
    """Pull the weekly rotation out of the page.

    Each day's block is scoped by its own `<h1>`, so we only take the
    `sqsrte-large` paragraphs that are siblings of that heading. The page has
    more of them further down inside per-day "Want to do more?" panels, and
    those are supplemental ideas rather than the day's workout.
    """
    soup = BeautifulSoup(html, "html.parser")
    days: dict[str, list[str]] = {}

    for heading in soup.find_all("h1"):
        name = heading.get_text(" ", strip=True)
        if name not in WEEKDAYS or name in days:
            continue
        lines = [
            text
            for sibling in heading.find_next_siblings("p")
            if "sqsrte-large" in (sibling.get("class") or [])
            and (text := sibling.get_text(" ", strip=True))
        ]
        if lines:
            days[name] = lines

    return Rotation(days=days)


async def fetch_rotation() -> Rotation | None:
    """Fetch and parse the routine page, or None if it's unreachable/unusable."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(
                PAGE_URL,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=_REQUEST_TIMEOUT,
            )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        logger.debug("Hybrid Calisthenics returned HTTP %d.", response.status_code)
        return None

    rotation = parse_rotation(response.text)
    # A page we could fetch but not understand means the markup moved. Better to
    # keep serving the cached rotation than to overwrite it with nothing.
    if not rotation.days:
        logger.warning("Could not find any days in the Hybrid Calisthenics page.")
        return None
    return rotation
