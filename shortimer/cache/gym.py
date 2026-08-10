"""Mongo-backed cache of a gym's workouts, whichever platform they came from.

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

Nothing here knows what a Wodify or a SugarWOD is. Which provider to fetch
with, and whether a stored connection is complete enough to try, both come from
`gym_providers`; this module owns caching, refresh scheduling and pre-parsing.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel

from shortimer.cache.crypto import decrypt
from shortimer.cache.db import get_gym_cache_collection, get_users_collection
from shortimer.cache.parse import SOURCE_GYM, find_parse, remember_parse
from shortimer.model.gym import PROVIDER_PRIORITY, GymProvider, GymWod
from shortimer.model.user import User
from shortimer.service.gym_providers import spec_for
from shortimer.service.llm import parse_workout_text

logger = logging.getLogger(__name__)

#: How many days back to keep and offer. Matches the crossfit.com feed.
CACHE_DAYS = 14
#: Skip a refresh newer than this, so several instances don't all re-fetch.
MIN_REFRESH_INTERVAL = timedelta(hours=12)
#: How long the background task sleeps between sweeps.
REFRESH_INTERVAL_SECONDS = 6 * 60 * 60


def gym_fingerprint(credential: str, provider: GymProvider) -> str:
    """A stable, non-reversible id for the gym behind a credential.

    The provider is mixed in so the same gym reached two ways doesn't collide —
    two routes return differently formatted text for the same workout, and
    conflating them would serve whichever was written last.
    """
    digest = hashlib.sha256(f"{provider.value}:{credential}".encode()).hexdigest()
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
        "provider": wod.provider.value,
        "fetched_at": datetime.now(UTC),
    }


def _from_document(doc: dict[str, object]) -> GymWod:
    return GymWod(
        date=date.fromisoformat(str(doc["date"])),
        title=str(doc["title"]),
        text=str(doc["text"]),
        url=str(doc.get("url") or ""),
        provider=GymProvider(str(doc["provider"])),
    )


async def last_refreshed_at(fingerprint: str) -> datetime | None:
    doc = await get_gym_cache_collection().find_one({"gym": fingerprint}, sort=[("fetched_at", -1)])
    if doc is None:
        return None
    fetched = doc.get("fetched_at")
    if not isinstance(fetched, datetime):
        return None
    return fetched if fetched.tzinfo else fetched.replace(tzinfo=UTC)


async def read_cached(fingerprint: str, days: int) -> list[GymWod]:
    """Most recent `days` cached workouts for one gym, newest first."""
    cursor = get_gym_cache_collection().find({"gym": fingerprint}).sort("date", -1).limit(days)
    return [_from_document(doc) async for doc in cursor]


async def _store(wods: list[GymWod], fingerprint: str) -> int:
    collection = get_gym_cache_collection()
    for wod in wods:
        document = _to_document(wod, fingerprint)
        await collection.replace_one({"_id": document["_id"]}, document, upsert=True)
    return len(wods)


# --- Resolving a user's configuration into a fetch ---------------------------


class GymSource:
    """A user's configured gym, resolved and ready to fetch."""

    def __init__(
        self, *, provider: GymProvider, credential: str, location: str, program: str
    ) -> None:
        self.provider = provider
        self.credential = credential
        self.location = location
        self.program = program
        self.fingerprint = gym_fingerprint(credential, provider)


def resolve_source(user: User) -> GymSource | None:
    """The gym this user's config points at, or None if none is usable.

    A user may store several connections — someone who runs a gym on SugarWOD
    and attends another on Wodify is not a strange case — but only one feeds
    the home page, because "your gym" is singular in the UI. Ties break on
    `PROVIDER_PRIORITY` rather than on storage order, so the answer doesn't
    depend on which connection happened to be saved first.
    """
    for provider in PROVIDER_PRIORITY:
        connection = user.config.connection(provider)
        if connection is None or connection.credential is None:
            continue
        if not spec_for(provider).is_usable(connection):
            continue
        credential = decrypt(connection.credential)
        if not credential:
            continue
        return GymSource(
            provider=provider,
            credential=credential,
            location=connection.location or "",
            program=connection.program or "",
        )
    return None


async def _fetch(source: GymSource, days: int) -> list[GymWod]:
    return await spec_for(source.provider).fetch(
        days,
        credential=source.credential,
        location=source.location,
        program=source.program,
    )


async def refresh(source: GymSource, *, force: bool = False) -> int:
    """Fetch this gym and upsert the results. Returns rows written.

    A failed fetch leaves the existing cache alone, so an unreachable platform
    degrades to stale data rather than an empty feed.
    """
    if not force:
        last = await last_refreshed_at(source.fingerprint)
        if last is not None and datetime.now(UTC) - last < MIN_REFRESH_INTERVAL:
            return 0

    wods = await _fetch(source, CACHE_DAYS)
    if not wods:
        logger.info(
            "%s returned no workouts for gym %s; keeping cache.",
            source.provider.value,
            source.fingerprint,
        )
        return 0
    written = await _store(wods, source.fingerprint)
    logger.info(
        "Cached %d %s day(s) for gym %s.", written, source.provider.value, source.fingerprint
    )
    return written


async def ensure_parsed(fingerprint: str, limit: int = CACHE_DAYS) -> int:
    """Warm the shared parse pool with this gym's cached days.

    Same rationale as the crossfit.com pre-parse: a gym's workout gets loaded
    by many of its members, and parsing is per-owner, so doing it here means
    one model call per workout instead of one per member.
    """
    parsed = 0
    cursor = get_gym_cache_collection().find({"gym": fingerprint}).sort("date", -1).limit(limit)
    async for doc in cursor:
        text = str(doc.get("text") or "")
        if not text or await find_parse(text) is not None:
            continue
        title = str(doc.get("title") or "") or None
        try:
            workout = await parse_workout_text(text, name_hint=title, purpose="prewarm:gym")
        except Exception:  # one bad day shouldn't stop the rest
            logger.exception("Could not pre-parse gym day %s", doc.get("date"))
            continue
        await remember_parse(workout, source=SOURCE_GYM)
        parsed += 1

    if parsed:
        logger.info("Pre-parsed %d gym workout(s).", parsed)
    return parsed


async def get_wods(source: GymSource, days: int) -> list[GymWod]:
    """Cached workouts for this gym, filling the cache on first use."""
    cached = await read_cached(source.fingerprint, days)
    if cached:
        return cached
    await refresh(source, force=True)
    return await read_cached(source.fingerprint, days)


# --- Reporting a connection's health -----------------------------------------


class ConnectionHealth(BaseModel):
    """What the settings screen shows about one stored connection.

    Every fetcher swallows its errors so that one bad day can't empty a feed.
    That is right for the feed and useless for someone trying to work out why
    their gym never appears — the symptom of a wrong credential and of a gym
    that simply didn't post are identical. So the cache's own record is
    surfaced here: "never fetched" is the answer they need, and nothing else in
    the app was telling them.
    """

    provider: GymProvider
    #: None when this gym has never been fetched successfully.
    last_fetched_at: datetime | None = None
    cached_days: int = 0


async def connection_health(user: User) -> list[ConnectionHealth]:
    """Fetch health for every connection the user has stored a credential for."""
    collection = get_gym_cache_collection()
    health: list[ConnectionHealth] = []
    for connection in user.config.gyms:
        if connection.credential is None:
            continue
        credential = decrypt(connection.credential)
        if not credential:
            # Encrypted under a key that has since been rotated out. Reported
            # as never-fetched rather than skipped, so it doesn't vanish from
            # the one screen whose job is explaining an absent feed.
            health.append(ConnectionHealth(provider=connection.provider))
            continue
        fingerprint = gym_fingerprint(credential, connection.provider)
        health.append(
            ConnectionHealth(
                provider=connection.provider,
                last_fetched_at=await last_refreshed_at(fingerprint),
                cached_days=await collection.count_documents({"gym": fingerprint}),
            )
        )
    return health


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
            logger.exception("Skipping unreadable user document during gym refresh.")
            continue
        source = resolve_source(user)
        if source is None or source.fingerprint in seen:
            continue
        seen.add(source.fingerprint)
        try:
            await refresh(source)
            await ensure_parsed(source.fingerprint)
        except Exception:  # one gym failing shouldn't stop the rest
            logger.exception("Gym refresh failed for gym %s.", source.fingerprint)
            continue
        refreshed += 1
    return refreshed
