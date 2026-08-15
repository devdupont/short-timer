"""Fixed-window rate limiting, backed by Mongo.

State lives in the database rather than in process memory so limits hold
across restarts and across instances. That matters for the two things being
protected: brute-force attempts against the shared passcode, and spend on
Anthropic calls — neither of which should reset because a container recycled
or a second replica came up.

Windows are fixed rather than sliding, which allows a burst of up to 2x the
limit across a window boundary. That's an acceptable trade for a single
indexed upsert per request; these limits exist to bound abuse and cost, not
to shape traffic precisely.
"""

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status

from shortimer.auth.session import current_owner
from shortimer.cache.db import get_rate_limit_collection
from shortimer.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimit:
    """A ceiling of `limit` events per `window_seconds`, per subject."""

    scope: str
    limit: int
    window_seconds: int


def subject_for(request: Request, owner_id: str) -> str:
    """Who a limit is counted against.

    The account, now that every session names a real one. That's what we
    actually want to limit: an account is what has a budget, and unlike an
    address it doesn't lump a whole gym's WiFi together or let one person
    reset their quota by switching networks.

    `request` is still taken because the unauthenticated limits — login,
    registration, forgot-password — have no account to charge and fall back to
    the address; see `client_ip`.
    """
    return f"owner:{owner_id}"


def client_ip(request: Request) -> str:
    """The caller's address, trusting only what the deployment says to trust.

    Rate limits are counted per address, so getting this wrong is a bypass:
    if we believed a client-supplied header, a guesser could defeat the login
    limit by sending a different value on every attempt.

    Proxies *append* to X-Forwarded-For, so the leftmost entry is whatever the
    caller sent and the rightmost entries are what our own infrastructure
    added. We therefore count in from the right by the number of trusted hops
    rather than taking the first entry. With nothing configured we ignore
    headers altogether and use the socket peer.
    """
    settings = get_settings()

    # A single-value platform header, where present, is unambiguous.
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header)
        if value and value.strip():
            return value.strip()

    hops = settings.trusted_proxy_hops
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            # Fewer entries than trusted hops means the header didn't come
            # through the expected path; fall back rather than trust it.
            if len(parts) >= hops:
                return parts[-hops]

    return request.client.host if request.client else "unknown"


async def peek(rate_limit: RateLimit, subject: str) -> None:
    """Raise 429 if the subject is already over its limit, without counting.

    Lets a caller gate on the limit before deciding whether the attempt is
    worth charging for — see the login route, which only charges failures.
    """
    if not get_settings().rate_limit_enabled:
        return

    now = time.time()
    window_start = math.floor(now / rate_limit.window_seconds) * rate_limit.window_seconds
    doc = await get_rate_limit_collection().find_one(
        {"_id": f"{rate_limit.scope}:{subject}:{window_start}"}
    )
    if int((doc or {}).get("count", 0)) >= rate_limit.limit:
        _too_many(rate_limit, subject, window_start + rate_limit.window_seconds, now)


async def enforce(rate_limit: RateLimit, subject: str) -> None:
    """Count one event, raising 429 once the subject is over its limit."""
    if not get_settings().rate_limit_enabled:
        return

    now = time.time()
    window_start = math.floor(now / rate_limit.window_seconds) * rate_limit.window_seconds
    resets_at = window_start + rate_limit.window_seconds

    key = f"{rate_limit.scope}:{subject}:{window_start}"
    doc = await get_rate_limit_collection().find_one_and_update(
        {"_id": key},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": _expiry(resets_at)}},
        upsert=True,
        return_document=True,
    )

    count = int((doc or {}).get("count", 1))
    if count > rate_limit.limit:
        _too_many(rate_limit, subject, resets_at, now)


def _too_many(rate_limit: RateLimit, subject: str, resets_at: float, now: float) -> None:
    """Log and raise the 429 shared by `peek` and `enforce`."""
    retry_after = max(1, int(resets_at - now))
    logger.warning(
        "Rate limit hit: scope=%s subject=%s limit=%d",
        rate_limit.scope,
        subject,
        rate_limit.limit,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Please wait a moment and try again.",
        headers={"Retry-After": str(retry_after)},
    )


def _expiry(resets_at: float) -> datetime:
    """When the window's counter becomes garbage, for the TTL index to reap."""
    return datetime.fromtimestamp(resets_at, tz=UTC)


def login_limit() -> RateLimit:
    """Failed sign-in attempts allowed per subject in 15 minutes."""
    return RateLimit("login", get_settings().login_attempts_per_15_min, 15 * 60)


def llm_subject_limit() -> RateLimit:
    """Model calls allowed per subject per hour."""
    return RateLimit("llm", get_settings().llm_calls_per_hour_per_subject, 3600)


def llm_global_limit() -> RateLimit:
    """Model calls allowed across the whole deployment per hour — the spend backstop."""
    return RateLimit("llm-global", get_settings().llm_calls_per_hour_global, 3600)


def write_limit() -> RateLimit:
    """Mutating requests allowed per subject per minute."""
    return RateLimit("write", get_settings().writes_per_minute_per_subject, 60)


async def writes_allowed(request: Request, owner_id: str = Depends(current_owner)) -> str:
    """Dependency for mutating routes: bounds write traffic, yields the owner.

    Returning the owner means an endpoint swaps `Depends(current_owner)` for
    this and gets the limit applied, rather than needing a separate parameter
    that's easy to forget on a new route.
    """
    await enforce(write_limit(), subject_for(request, owner_id))
    return owner_id
