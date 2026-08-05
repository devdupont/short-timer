"""Reading the event stream back.

Two endpoints, because there are two questions with very different blast
radii. `/me` answers "what have *I* used", which is safe for anyone to see and
is the shape the eventual per-plan usage meter and gym-owner view are built
from. `/operator` answers "what is this costing and who is using it", which is
nobody's business but the operator's.

**There are no roles yet**, and the app authenticates everyone as one shared
passcode user. Rather than pretend otherwise, `/operator` gates on an explicit
allowlist of user ids that defaults to *empty* — so it is off until someone
deliberately turns it on, and it keeps working unchanged the day real accounts
arrive. A `require_session` gate alone would have handed the Anthropic bill to
anyone who knows the passcode.

No UI consumes these yet. That's deliberate: events can't be backfilled, so the
recording had to start now, whereas a dashboard on top of good data is cheap
whenever it's wanted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from short_timer.auth import current_owner, require_session
from short_timer.config import get_settings
from short_timer.metrics import (
    active_owners,
    event_totals,
    feed_health,
    model_spend,
    parse_breakdown,
)

router = APIRouter(prefix="/api/metrics", tags=["metrics"], dependencies=[Depends(require_session)])

#: Longest window a caller may ask for. Beyond the retention window the answer
#: is silently partial, which is worse than being told no.
_MAX_DAYS = 365
_DEFAULT_DAYS = 30


class ParseUsage(BaseModel):
    """How a caller's parses resolved, and what that saved.

    `avoided_model_calls` is the whole argument for the parse pool stated as a
    number: every hit is a model call that didn't happen. Under the pricing in
    `docs/pricing.md` it's also the difference between a free tier that costs
    nothing to serve and one that doesn't.
    """

    library_hits: int = 0
    pool_hits: int = 0
    model_calls: int = 0
    failed: int = 0
    avoided_model_calls: int = 0
    #: Share of resolved parses served without calling the model, 0 to 1.
    cache_hit_rate: float = 0.0


class MeMetrics(BaseModel):
    days: int
    parses: ParseUsage
    workouts_started: int = 0
    workouts_completed: int = 0
    #: Share of started workouts that reached the end, 0 to 1. Starts alone
    #: can't tell programming that fits from programming people abandon.
    completion_rate: float = 0.0


class OperatorMetrics(BaseModel):
    days: int
    parses: ParseUsage
    #: Token totals per model, priced at today's rates rather than at the rate
    #: in force when the call was made — see `metrics.model_spend`.
    spend: dict[str, Any]
    events: dict[str, int]
    feeds: list[dict[str, Any]]
    active_owners: int = 0


def _usage(breakdown: dict[str, int]) -> ParseUsage:
    library = breakdown.get("library_hit", 0)
    pool = breakdown.get("pool_hit", 0)
    calls = breakdown.get("model_call", 0)
    failed = breakdown.get("failed", 0)
    avoided = library + pool
    # Failures are excluded from the denominator: a parse the model couldn't
    # complete was neither served from cache nor a cache miss, and counting it
    # either way would move a rate that's meant to describe the cache.
    resolved = avoided + calls
    return ParseUsage(
        library_hits=library,
        pool_hits=pool,
        model_calls=calls,
        failed=failed,
        avoided_model_calls=avoided,
        cache_hit_rate=round(avoided / resolved, 4) if resolved else 0.0,
    )


async def _require_operator(owner_id: str = Depends(current_owner)) -> str:
    admins = get_settings().metrics_admin_user_ids
    if not admins or owner_id not in admins:
        # 404 rather than 403: an endpoint the caller may not use shouldn't
        # confirm it exists, and this one names what the deployment spends.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return owner_id


@router.get("/me", response_model=MeMetrics)
async def my_metrics(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    owner_id: str = Depends(current_owner),
) -> MeMetrics:
    """The caller's own usage. No cost figures — those are the operator's."""
    totals = await event_totals(days, owner_id=owner_id)
    started = totals.get("workout_started", 0)
    completed = totals.get("workout_completed", 0)
    return MeMetrics(
        days=days,
        parses=_usage(await parse_breakdown(days, owner_id=owner_id)),
        workouts_started=started,
        workouts_completed=completed,
        # Can exceed 1 across a window boundary — a workout started just before
        # it and finished just inside counts once here and not at all there.
        # Clamped rather than explained away in every consumer.
        completion_rate=round(min(completed / started, 1.0), 4) if started else 0.0,
    )


@router.get("/operator", response_model=OperatorMetrics)
async def operator_metrics(
    days: int = Query(_DEFAULT_DAYS, ge=1, le=_MAX_DAYS),
    owner_id: str = Depends(_require_operator),
) -> OperatorMetrics:
    """Everything, across every user. Allowlisted, and empty by default."""
    return OperatorMetrics(
        days=days,
        parses=_usage(await parse_breakdown(days)),
        spend=await model_spend(days),
        events=await event_totals(days),
        feeds=await feed_health(days),
        active_owners=await active_owners(days),
    )
