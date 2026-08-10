"""Fetch a gym's posted workouts from Wodify.

Two routes, because access to gym programming is per-person and per-gym:

- **Owner** (`fetch_owner_wod`) — the official Program API, authenticated with
  an `x-api-key` a gym admin generates. Returns JSON whose `FormattedWOD` field
  holds the workout as an HTML fragment. Filters on exact location and program
  names.
- **Member** (`fetch_member_wod`) — the public whiteboard, a plain web page the
  gym opts into publishing. No authentication; the `WhiteboardKey` in the URL
  is what the gym hands out.

Both end up as HTML, so both funnel through `html_text.extract_text` and come
back as the same `GymWod`. A day that's missing, unpublished, or fails upstream
is skipped rather than raising — same contract as the crossfit.com intake, and
for the same reason: one bad day shouldn't empty the feed.

Neither route has been exercised against a live gym yet. The shapes here follow
Wodify's published documentation, and the JSON field names in particular
(`APIWod` / `FormattedWOD`) are the part most worth re-checking against a real
key — hence `_first_str`, which tolerates a couple of plausible spellings
rather than hard-failing on the first surprise.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from shortimer.model.gym import GymProvider, GymWod
from shortimer.util.html_text import extract_text

logger = logging.getLogger(__name__)

USER_AGENT = "shortimer/0.1 (+https://github.com/devdupont/shortimer)"

_PROGRAM_API_URL = "https://api.wodify.com/v1/workouts/formattedworkout"
_WHITEBOARD_URL = "https://app.wodify.com/Performance/PublicWhiteboard.aspx"
_REQUEST_TIMEOUT = 20.0

#: Guard against a redirect-to-login or an error page being parsed as a
#: workout. Real entries comfortably clear this; a "please sign in" page
#: reduces to almost nothing once chrome is stripped.
_MIN_TEXT_LENGTH = 20


def _first_str(payload: dict[str, Any], *names: str) -> str:
    """First non-empty string among `names`, searched case-insensitively.

    Wodify's documented casing and what a given tenant actually returns have
    been known to differ, and an unchecked `KeyError` here would take out the
    whole feed rather than one field.
    """
    lowered = {key.lower(): value for key, value in payload.items()}
    for name in names:
        value = lowered.get(name.lower())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _unwrap_wod(payload: Any) -> dict[str, Any]:
    """Find the workout object inside whatever envelope the API returned."""
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else {}
    if not isinstance(payload, dict):
        return {}
    # Documented shape is {"APIWod": {...}}; some endpoints answer with the
    # object directly, and bulk queries wrap a list.
    for key in ("APIWod", "apiWod", "Wod", "wod"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            return nested[0]
    return payload


def _default_title(day: date) -> str:
    return day.strftime("%A %y%m%d")


async def fetch_owner_wod(
    client: httpx.AsyncClient,
    day: date,
    *,
    api_key: str,
    location: str,
    program: str,
) -> GymWod | None:
    """One day via the Program API, or None if it's missing or the call fails."""
    try:
        response = await client.get(
            _PROGRAM_API_URL,
            params={"date": day.isoformat(), "location": location, "program": program},
            headers={
                "x-api-key": api_key,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        # 401/403 means the key is wrong or revoked — worth a log, since the
        # user can fix it in Settings, but not worth raising into their feed.
        if response.status_code in (401, 403):
            logger.warning("Wodify rejected the API key (%s).", response.status_code)
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    wod = _unwrap_wod(payload)
    if not wod:
        return None

    html = _first_str(wod, "FormattedWOD", "FormattedWod", "formatted_wod", "Wod")
    if not html:
        return None
    text = extract_text(html)
    if len(text) < _MIN_TEXT_LENGTH:
        return None

    title = _first_str(wod, "Title", "Name", "ProgramName") or _default_title(day)
    return GymWod(
        date=day,
        title=title,
        text=text,
        url=_whiteboard_link(day, program=program),
        provider=GymProvider.WODIFY_OWNER,
    )


def _whiteboard_link(day: date, *, program: str = "") -> str:
    """A "view on Wodify" pointer for the day.

    Deliberately **without** the WhiteboardKey. This string is cached and
    returned to clients, and the key is a credential we encrypt at rest and
    mask on read — embedding it here would hand it straight back out in a URL
    and undo both. The result is a pointer rather than a deep link, which is
    the right trade: nothing else in the app returns a credential to the
    browser either.
    """
    link = f"{_WHITEBOARD_URL}?Date={day:%m/%d/%Y}"
    return f"{link}&ProgramName={program}" if program else link


async def fetch_member_wod(
    client: httpx.AsyncClient,
    day: date,
    *,
    whiteboard_key: str,
    location: str = "",
    program: str = "",
) -> GymWod | None:
    """One day from the public whiteboard, or None if nothing is published."""
    params: dict[str, str] = {"WhiteboardKey": whiteboard_key, "Date": f"{day:%m/%d/%Y}"}
    if location:
        params["LocationName"] = location
    if program:
        params["ProgramName"] = program

    try:
        response = await client.get(
            _WHITEBOARD_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None

    text = extract_text(response.text)
    # A gym that hasn't published this day still serves a 200 with an empty
    # board, which is indistinguishable from a fetch failure downstream — and
    # should be, since both mean "nothing to show".
    if len(text) < _MIN_TEXT_LENGTH:
        return None

    return GymWod(
        date=day,
        title=_default_title(day),
        text=text,
        url=_whiteboard_link(day, program=program),
        provider=GymProvider.WODIFY_MEMBER,
    )


async def fetch_recent_owner_wods(
    days: int, *, api_key: str, location: str, program: str, today: date | None = None
) -> list[GymWod]:
    anchor = today or datetime.now().date()
    targets = [anchor - timedelta(days=offset) for offset in range(max(1, days))]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *(
                fetch_owner_wod(client, day, api_key=api_key, location=location, program=program)
                for day in targets
            )
        )
    return [wod for wod in results if wod is not None]


async def fetch_recent_member_wods(
    days: int,
    *,
    whiteboard_key: str,
    location: str = "",
    program: str = "",
    today: date | None = None,
) -> list[GymWod]:
    anchor = today or datetime.now().date()
    targets = [anchor - timedelta(days=offset) for offset in range(max(1, days))]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *(
                fetch_member_wod(
                    client,
                    day,
                    whiteboard_key=whiteboard_key,
                    location=location,
                    program=program,
                )
                for day in targets
            )
        )
    return [wod for wod in results if wod is not None]
