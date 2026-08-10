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

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import BaseModel

from shortimer.cache._feed import FeedCache
from shortimer.cache.crypto import decrypt
from shortimer.cache.db import get_users_collection
from shortimer.cache.parse import SOURCE_GYM
from shortimer.model.feed_cache import GymCacheEntry
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


class _Cache(FeedCache[GymCacheEntry]):
    """`FeedCache` bound to `GymCacheEntry`."""

    document_model = GymCacheEntry


def gym_fingerprint(credential: str, provider: GymProvider) -> str:
    """A stable, non-reversible id for the gym behind a credential.

    The provider is mixed in so the same gym reached two ways doesn't collide —
    two routes return differently formatted text for the same workout, and
    conflating them would serve whichever was written last.
    """
    digest = hashlib.sha256(f"{provider.value}:{credential}".encode()).hexdigest()
    return digest[:32]


def _to_wod(entry: GymCacheEntry) -> GymWod:
    """A cached row as the `GymWod` shape callers work with."""
    return GymWod(
        date=entry.date, title=entry.title, text=entry.text, url=entry.url, provider=entry.provider
    )


async def last_refreshed_at(fingerprint: str) -> datetime | None:
    """When this gym's cache was most recently written, if ever."""
    return await _Cache.last_refreshed_at({"gym": fingerprint})


async def read_cached(fingerprint: str, days: int) -> list[GymWod]:
    """Most recent `days` cached workouts for one gym, newest first."""
    cursor = GymCacheEntry.find(GymCacheEntry.gym == fingerprint).sort("-date").limit(days)
    return [_to_wod(entry) async for entry in cursor]


async def _store(wods: list[GymWod], fingerprint: str) -> int:
    """Upsert `wods` for `fingerprint`. Returns how many were written."""
    for wod in wods:
        await GymCacheEntry(
            id=f"{fingerprint}:{wod.date.isoformat()}",
            gym=fingerprint,
            date=wod.date,
            title=wod.title,
            text=wod.text,
            url=wod.url,
            provider=wod.provider,
        ).save()
    return len(wods)


# --- Resolving a user's configuration into a fetch ---------------------------


@dataclass
class GymSource:
    """A user's configured gym, resolved and ready to fetch."""

    provider: GymProvider
    credential: str
    location: str
    program: str
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        """Derive `fingerprint` from the credential and provider."""
        self.fingerprint = gym_fingerprint(self.credential, self.provider)


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
    """Dispatch to the provider spec for `source` and fetch `days` of workouts."""
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
    if not force and await _Cache.is_fresh(MIN_REFRESH_INTERVAL, {"gym": source.fingerprint}):
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
    cursor = GymCacheEntry.find(GymCacheEntry.gym == fingerprint).sort("-date").limit(limit)
    candidates = [(entry.text, entry.title or None) async for entry in cursor]
    # A gym's programming stays on the ordinary user-retention clock even when
    # a member pastes it first — unlike crossfit.com/Concept2/Hybrid, it isn't
    # a source everyone shares, so a match here is never promoted permanent.
    parsed = await _Cache.warm_parse_pool(
        candidates,
        parse=parse_workout_text,
        source=SOURCE_GYM,
        purpose="prewarm:gym",
        promote_existing=False,
    )
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
                cached_days=await GymCacheEntry.find(GymCacheEntry.gym == fingerprint).count(),
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
