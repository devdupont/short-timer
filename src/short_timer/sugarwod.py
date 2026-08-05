"""Fetch a gym's posted workouts from SugarWOD.

One route today — the v2 Program API, authenticated with a key a gym admin
generates under *Settings → Developer Keys*. It answers in JSON:API form, so a
workout arrives as `{"type": "workouts", "id": ..., "attributes": {...}}` and
the fields we want live under `attributes`.

**A second route is documented but not built.** SugarWOD publishes a public
per-affiliate feed (the thing gyms embed in their own websites), which would be
the member-side equivalent of Wodify's public whiteboard and needs no
credential at all. Every documented form of that URL currently answers
`Invalid format.` regardless of the gym id, so its shape can't be pinned down
without a real gym account — see `docs/integrations.md`. When someone confirms
it, it becomes another `GymProvider` and a second fetcher here; nothing else
has to change.

Like `wodify`, the attribute names below come from SugarWOD's documentation
rather than from a live response, so reads go through `_first` and `_as_date`,
which tolerate several plausible spellings instead of raising on the first
surprise. A day that's missing or fails upstream is skipped rather than raised:
one bad day shouldn't empty the feed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from short_timer.html_text import extract_text
from short_timer.models import GymProvider, GymWod

logger = logging.getLogger(__name__)

USER_AGENT = "short-timer/0.1 (+https://github.com/devdupont/short-timer)"

_API_URL = "https://api.sugarwod.com/v2/workouts"
_REQUEST_TIMEOUT = 20.0

#: The API rejects a `dates` range wider than this, so a longer window is
#: fetched as several requests. Documented as a 7-day limit.
MAX_RANGE_DAYS = 7

#: Same guard as the Wodify intake: a login redirect or error page reduces to
#: almost nothing once chrome is stripped, and shouldn't reach the parser.
_MIN_TEXT_LENGTH = 20


def _first(attributes: dict[str, Any], *names: str) -> str:
    """First non-empty string among `names`, searched case-insensitively.

    SugarWOD's docs and its actual payloads have not been cross-checked, and
    an unchecked `KeyError` here would take out the whole feed rather than one
    field. Also tolerates camelCase vs snake_case, which JSON:API producers
    are inconsistent about.
    """
    lowered = {key.lower().replace("_", ""): value for key, value in attributes.items()}
    for name in names:
        value = lowered.get(name.lower().replace("_", ""))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_date(attributes: dict[str, Any], fallback: date) -> date:
    """The workout's own date, however it chose to express it.

    SugarWOD uses a `date_int` of the form 20260804 in its query parameters, so
    that's the most likely storage form, but an ISO string is just as plausible
    in a response body. Anything unreadable falls back to the day we asked for,
    which is right often enough and never wrong enough to matter — the date is
    a label here, not a key.
    """
    for key in ("dateInt", "date_int", "date", "scheduledDate", "scheduled_date"):
        value = attributes.get(key)
        if isinstance(value, int):
            value = str(value)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        try:
            if len(text) == 8 and text.isdigit():
                return datetime.strptime(text, "%Y%m%d").date()
            return date.fromisoformat(text[:10])
        except ValueError:
            continue
    return fallback


def _default_title(day: date) -> str:
    return day.strftime("%A %y%m%d")


def _to_wod(item: dict[str, Any], fallback_day: date) -> GymWod | None:
    """One JSON:API resource object into a `GymWod`, or None if unusable."""
    attributes = item.get("attributes")
    if not isinstance(attributes, dict):
        return None

    # `description` is the workout itself. SugarWOD lets coaches paste rich
    # text, so it may arrive as an HTML fragment — `extract_text` is a no-op on
    # text that has no tags, so it's safe to run either way.
    body = _first(attributes, "description", "descriptionHtml", "body", "notes")
    if not body:
        return None
    text = extract_text(body)
    if len(text) < _MIN_TEXT_LENGTH:
        return None

    day = _as_date(attributes, fallback_day)
    title = _first(attributes, "title", "name", "trackName") or _default_title(day)
    return GymWod(date=day, title=title, text=text, provider=GymProvider.SUGARWOD_OWNER)


def _windows(start: date, end: date) -> list[tuple[date, date]]:
    """Split an inclusive range into chunks the API will accept."""
    spans: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=MAX_RANGE_DAYS - 1), end)
        spans.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return spans


async def fetch_window(
    client: httpx.AsyncClient,
    start: date,
    end: date,
    *,
    api_key: str,
    track_id: str = "",
) -> list[GymWod]:
    """Workouts in one date window, or [] if it's empty or the call fails."""
    params: dict[str, str] = {"dates": f"{start:%Y%m%d}-{end:%Y%m%d}"}
    if track_id:
        params["track_id"] = track_id

    try:
        response = await client.get(
            _API_URL,
            params=params,
            headers={
                # Sent as a header rather than the `?apiKey=` form the docs also
                # allow: a key in a query string ends up in access logs and
                # proxy history, and this one reads a gym's whole programming.
                "Authorization": api_key,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError:
        return []

    if response.status_code != 200:
        # 401/403 means the key is wrong or revoked, and 429 means we're asking
        # too often — both are worth a log because the user can act on them,
        # neither is worth raising into their feed.
        if response.status_code in (401, 403):
            logger.warning("SugarWOD rejected the API key (%s).", response.status_code)
        elif response.status_code == 429:
            logger.warning("SugarWOD rate limited us.")
        return []

    try:
        payload = response.json()
    except ValueError:
        return []

    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []

    wods = [_to_wod(item, start) for item in data if isinstance(item, dict)]
    return [wod for wod in wods if wod is not None]


async def fetch_recent_owner_wods(
    days: int,
    *,
    api_key: str,
    track_id: str = "",
    today: date | None = None,
) -> list[GymWod]:
    """The last `days` days of a gym's programming, newest first.

    Unlike the Wodify intake, which asks for one day per request, this asks for
    a range — so a fortnight is two calls rather than fourteen. Windows are
    fetched concurrently and deduplicated by date, since a workout that appears
    in two tracks would otherwise show up twice.
    """
    anchor = today or datetime.now().date()
    start = anchor - timedelta(days=max(1, days) - 1)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        batches = await asyncio.gather(
            *(
                fetch_window(client, span_start, span_end, api_key=api_key, track_id=track_id)
                for span_start, span_end in _windows(start, anchor)
            )
        )

    by_day: dict[date, GymWod] = {}
    for batch in batches:
        for wod in batch:
            by_day.setdefault(wod.date, wod)
    return sorted(by_day.values(), key=lambda wod: wod.date, reverse=True)
