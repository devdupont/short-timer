"""Mongo-backed cache of a gym's Wodify workouts.

Mirrors `wod_cache`, with one structural difference: crossfit.com publishes one
workout a day for everybody, so that cache is global. A gym publishes to its
own members, so this one is keyed per gym.

**The cache key has to be gym-unique or the feed leaks.** Keying on
location+program alone would be a real bug: "Main"/"CrossFit" is not a rare
pair, and two unrelated gyms sharing it would serve each other's workouts. So
entries are keyed on a fingerprint of the *credential* — the whiteboard key or
API key — which is the only thing that actually identifies a gym. The
credential is hashed, never stored: this collection holds no secrets.

Fingerprinting the credential also gets the sharing right. Two members of one
gym hold the same whiteboard key, so they hit the same entries and the gym is
fetched once for both. Two admins with separately-issued API keys don't share,
which is the safe way round.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta

from short_timer.crypto import decrypt
from short_timer.db import get_users_collection, get_wodify_cache_collection
from short_timer.llm import parse_workout_text
from short_timer.models import User
from short_timer.parse_cache import SOURCE_WODIFY, find_parse, remember_parse
from short_timer.wodify import (
    GymWod,
    fetch_recent_member_wods,
    fetch_recent_owner_wods,
)

logger = logging.getLogger(__name__)

#: How many days back to keep and offer. Matches the crossfit.com feed.
CACHE_DAYS = 14
#: Skip a refresh newer than this, so several instances don't all re-fetch.
MIN_REFRESH_INTERVAL = timedelta(hours=12)
#: How long the background task sleeps between sweeps.
REFRESH_INTERVAL_SECONDS = 6 * 60 * 60

_MEMBER = "member"
_OWNER = "owner"


def gym_fingerprint(credential: str, route: str) -> str:
    """A stable, non-reversible id for the gym behind a credential.

    The route is mixed in so the same gym reached two ways doesn't collide —
    the two routes return differently formatted text for the same workout, and
    conflating them would serve whichever was written last.
    """
    digest = hashlib.sha256(f"{route}:{credential}".encode()).hexdigest()
    return digest[:32]


def _document_id(fingerprint: str, day: date) -> str:
    return f"{fingerprint}:{day.isoformat()}"


def _to_document(wod: GymWod, fingerprint: str) -> dict[str, object]:
    return {
        "_id": _document_id(fingerprint, wod.date),
        "gym": fingerprint,
        "date": wod.date.isoformat(),
        "title": wod.title,
        "text": wod.text,
        "url": wod.url,
        "fetched_at": datetime.now(UTC),
    }


def _from_document(doc: dict[str, object]) -> GymWod:
    return GymWod(
        date=date.fromisoformat(str(doc["date"])),
        title=str(doc["title"]),
        text=str(doc["text"]),
        url=str(doc["url"]),
    )


async def last_refreshed_at(fingerprint: str) -> datetime | None:
    doc = await get_wodify_cache_collection().find_one(
        {"gym": fingerprint}, sort=[("fetched_at", -1)]
    )
    if doc is None:
        return None
    fetched = doc.get("fetched_at")
    if not isinstance(fetched, datetime):
        return None
    return fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)


async def read_cached(fingerprint: str, days: int) -> list[GymWod]:
    """Most recent `days` cached workouts for one gym, newest first."""
    cursor = (
        get_wodify_cache_collection()
        .find({"gym": fingerprint})
        .sort("date", -1)
        .limit(days)
    )
    return [_from_document(doc) async for doc in cursor]


async def _store(wods: list[GymWod], fingerprint: str) -> int:
    collection = get_wodify_cache_collection()
    for wod in wods:
        document = _to_document(wod, fingerprint)
        await collection.replace_one({"_id": document["_id"]}, document, upsert=True)
    return len(wods)


# --- Resolving a user's configuration into a fetch ---------------------------


class GymSource:
    """A user's configured gym, resolved and ready to fetch."""

    def __init__(
        self, *, route: str, credential: str, location: str, program: str
    ) -> None:
        self.route = route
        self.credential = credential
        self.location = location
        self.program = program
        self.fingerprint = gym_fingerprint(credential, route)


def resolve_source(user: User) -> GymSource | None:
    """The gym this user's config points at, or None if it isn't usable.

    The member route wins when both are configured: someone who is both an
    admin and an athlete is looking at the same gym either way, and the
    whiteboard costs no API quota.
    """
    member = user.config.wodify_member
    if member.is_usable() and member.whiteboard_key is not None:
        key = decrypt(member.whiteboard_key)
        if key:
            return GymSource(
                route=_MEMBER,
                credential=key,
                location=member.location or "",
                program=member.program or "",
            )

    owner = user.config.wodify_owner
    if owner.is_usable() and owner.api_key is not None:
        key = decrypt(owner.api_key)
        if key:
            return GymSource(
                route=_OWNER,
                credential=key,
                location=owner.location or "",
                program=owner.program or "",
            )
    return None


async def _fetch(source: GymSource, days: int) -> list[GymWod]:
    if source.route == _MEMBER:
        return await fetch_recent_member_wods(
            days,
            whiteboard_key=source.credential,
            location=source.location,
            program=source.program,
        )
    return await fetch_recent_owner_wods(
        days,
        api_key=source.credential,
        location=source.location,
        program=source.program,
    )


async def refresh(source: GymSource, *, force: bool = False) -> int:
    """Fetch this gym and upsert the results. Returns rows written.

    A failed fetch leaves the existing cache alone, so an unreachable Wodify
    degrades to stale data rather than an empty feed.
    """
    if not force:
        last = await last_refreshed_at(source.fingerprint)
        if last is not None and datetime.now(UTC) - last < MIN_REFRESH_INTERVAL:
            return 0

    wods = await _fetch(source, CACHE_DAYS)
    if not wods:
        logger.info("Wodify returned no workouts for gym %s; keeping cache.", source.fingerprint)
        return 0
    written = await _store(wods, source.fingerprint)
    logger.info("Cached %d Wodify day(s) for gym %s.", written, source.fingerprint)
    return written


async def ensure_parsed(fingerprint: str, limit: int = CACHE_DAYS) -> int:
    """Warm the shared parse pool with this gym's cached days.

    Same rationale as the crossfit.com pre-parse: a gym's workout gets loaded
    by many of its members, and parsing is per-owner, so doing it here means
    one model call per workout instead of one per member.
    """
    parsed = 0
    cursor = (
        get_wodify_cache_collection()
        .find({"gym": fingerprint})
        .sort("date", -1)
        .limit(limit)
    )
    async for doc in cursor:
        text = str(doc.get("text") or "")
        if not text or await find_parse(text) is not None:
            continue
        title = str(doc.get("title") or "") or None
        try:
            workout = await parse_workout_text(text, name_hint=title)
        except Exception:  # one bad day shouldn't stop the rest
            logger.exception("Could not pre-parse Wodify day %s", doc.get("date"))
            continue
        await remember_parse(workout, source=SOURCE_WODIFY)
        parsed += 1

    if parsed:
        logger.info("Pre-parsed %d Wodify workout(s).", parsed)
    return parsed


async def get_wods(source: GymSource, days: int) -> list[GymWod]:
    """Cached workouts for this gym, filling the cache on first use."""
    cached = await read_cached(source.fingerprint, days)
    if cached:
        return cached
    await refresh(source, force=True)
    return await read_cached(source.fingerprint, days)


async def refresh_all_configured() -> int:
    """Refresh every gym any user has enabled. Returns gyms refreshed.

    Deduplicated by fingerprint so a gym with twenty members is fetched once.
    """
    seen: set[str] = set()
    refreshed = 0
    async for doc in get_users_collection().find({}):
        data = dict(doc)
        data["id"] = data.pop("_id")
        try:
            user = User(**data)
        except Exception:  # a malformed user shouldn't stop the sweep
            logger.exception("Skipping unreadable user document during Wodify refresh.")
            continue
        source = resolve_source(user)
        if source is None or source.fingerprint in seen:
            continue
        seen.add(source.fingerprint)
        try:
            await refresh(source)
            await ensure_parsed(source.fingerprint)
        except Exception:  # one gym failing shouldn't stop the rest
            logger.exception("Wodify refresh failed for gym %s.", source.fingerprint)
            continue
        refreshed += 1
    return refreshed
