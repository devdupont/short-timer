"""What the app did, recorded as events, so questions can be asked later.

Two audiences, one stream. The operator needs to know what the Anthropic bill
is made of and whether the caches are earning their keep; a gym on a paid plan
will want to know how much of its programming actually gets run. Those want
different *slices*, not different data, so everything lands in one collection
and the aggregation decides who sees what.

**Recording never breaks the thing it measures.** Every `record` swallows its
own failures, exactly like the feed fetchers: a metrics write that 500s a parse
the user already paid for would be a worse bug than the missing data point.

**Events store tokens, never dollars.** Cost is a *policy* applied to a fact,
and the policy moves — `claude-sonnet-5` is on introductory pricing until
2026-08-31, after which every dollar figure computed today becomes wrong. A
token count stays true forever, so prices live in `MODEL_PRICES` and are applied
when a question is asked. That also means correcting a price is a code change,
not a data migration.

What is deliberately *not* stored: workout text, credentials, or anything
derived from them. An event carries ids, counts and enum labels — enough to
answer "how much" and "how often", never "what did they paste".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from short_timer.config import get_settings
from short_timer.db import aggregate, get_events_collection

logger = logging.getLogger(__name__)


class EventType(StrEnum):
    """The things worth counting.

    Closed, like `FeedKind` and `GymProvider`: an aggregation can only report
    what it knows the shape of, so a free-form event name would be a row nobody
    ever queries.
    """

    #: An Anthropic request completed. Carries the token counts, and is the
    #: only event that costs money.
    MODEL_CALL = "model_call"
    #: A parse was asked for and resolved — from a library, from the shared
    #: pool, or by calling the model. The demand side of `MODEL_CALL`, and what
    #: makes the cache's value measurable rather than assumed.
    PARSE = "parse"
    #: A workout source was fetched. Reliability, not cost.
    FEED_REFRESH = "feed_refresh"
    #: Someone started a timer. The engagement number, and the one a gym cares
    #: about — programming nobody runs is programming nobody needs.
    WORKOUT_STARTED = "workout_started"
    #: A session was minted. The basis of any active-user count.
    LOGIN = "login"


class ParseOutcome(StrEnum):
    """Where a parse came from, cheapest first.

    The distinction between the two cache tiers is worth keeping: a library hit
    means this user had already saved it, a pool hit means *somebody* had
    already paid for it. Only the second one is evidence that sharing parses
    across users works.
    """

    LIBRARY_HIT = "library_hit"
    POOL_HIT = "pool_hit"
    MODEL_CALL = "model_call"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelPrice:
    """List price per million tokens."""

    input_per_mtok: float
    output_per_mtok: float
    #: Cache reads are ~0.1x input, writes ~1.25x. Only meaningful once prompt
    #: caching is switched on; until then these multiply zero.
    cache_read_multiplier: float = 0.1
    cache_write_multiplier: float = 1.25


#: List prices, per million tokens, as of 2026-08. Deliberately a table rather
#: than a single configured pair: the model is a setting, so a deployment can
#: change it, and pricing a Haiku call at Sonnet rates would quietly overstate
#: the bill. A model that isn't here reports tokens with no cost attached, which
#: is the honest answer — better than a confident wrong number.
MODEL_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5": ModelPrice(5.00, 25.00),
    "claude-sonnet-5": ModelPrice(3.00, 15.00),
    "claude-haiku-4-5": ModelPrice(1.00, 5.00),
}


def price_for(model: str) -> ModelPrice | None:
    return MODEL_PRICES.get(model)


def estimate_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Dollars for one call, or None when the model's price isn't known."""
    price = price_for(model)
    if price is None:
        return None
    billable_input = (
        input_tokens
        + cache_read_tokens * price.cache_read_multiplier
        + cache_write_tokens * price.cache_write_multiplier
    )
    return (
        billable_input * price.input_per_mtok + output_tokens * price.output_per_mtok
    ) / 1_000_000


# --- Recording ---------------------------------------------------------------


async def record(event_type: EventType, *, owner_id: str | None = None, **data: Any) -> None:
    """Write one event. Never raises, never blocks on failure.

    Awaited rather than fired into a background task: it's a single indexed
    insert against a connection pool that's already warm, and every call site
    has just finished something far more expensive. Awaiting keeps ordering
    obvious and avoids a pile of unawaited tasks whose failures nobody sees.
    """
    if not get_settings().metrics_enabled:
        return
    try:
        await get_events_collection().insert_one(
            {
                "type": event_type.value,
                "at": datetime.now(UTC),
                "owner_id": owner_id,
                "data": data,
            }
        )
    except Exception:  # a lost metric must never cost a request
        logger.warning("Could not record a %s event.", event_type.value, exc_info=True)


async def record_model_call(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    owner_id: str | None = None,
    purpose: str = "parse",
) -> None:
    """The only event that costs money. `purpose` separates a user's paste from
    a feed pre-warm, which are the same call with very different economics —
    one is demand, the other is an investment that pays back on every later
    reader of that workout."""
    await record(
        EventType.MODEL_CALL,
        owner_id=owner_id,
        model=model,
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


async def record_parse(
    *, outcome: ParseOutcome, owner_id: str | None = None, source: str = "user"
) -> None:
    await record(EventType.PARSE, owner_id=owner_id, outcome=outcome.value, source=source)


async def record_feed_refresh(*, feed: str, ok: bool, rows: int = 0) -> None:
    await record(EventType.FEED_REFRESH, feed=feed, ok=ok, rows=rows)


async def record_workout_started(*, owner_id: str, workout_id: str, mode: str) -> None:
    await record(EventType.WORKOUT_STARTED, owner_id=owner_id, workout_id=workout_id, mode=mode)


async def record_login(*, owner_id: str) -> None:
    await record(EventType.LOGIN, owner_id=owner_id)


# --- Asking questions --------------------------------------------------------


def _window(days: int) -> dict[str, Any]:
    return {"at": {"$gte": datetime.now(UTC) - timedelta(days=days)}}


async def _count_by(
    event_type: EventType, field: str, days: int, owner_id: str | None = None
) -> dict[str, int]:
    """How many events of one type, grouped by one field in `data`."""
    match: dict[str, Any] = {"type": event_type.value, **_window(days)}
    if owner_id is not None:
        match["owner_id"] = owner_id
    cursor = await aggregate(
        get_events_collection(),
        [
            {"$match": match},
            {"$group": {"_id": f"$data.{field}", "n": {"$sum": 1}}},
        ],
    )
    return {str(doc["_id"]): int(doc["n"]) async for doc in cursor}


async def parse_breakdown(days: int, owner_id: str | None = None) -> dict[str, int]:
    """Parses by outcome — the cache hit rate, in its raw form."""
    return await _count_by(EventType.PARSE, "outcome", days, owner_id)


async def model_spend(days: int, owner_id: str | None = None) -> dict[str, Any]:
    """Token totals per model over the window, priced at today's rates.

    Priced here rather than at write time, so a rate change re-prices history
    instead of leaving a mix of old and new dollars in one column.
    """
    match: dict[str, Any] = {"type": EventType.MODEL_CALL.value, **_window(days)}
    if owner_id is not None:
        match["owner_id"] = owner_id

    cursor = await aggregate(
        get_events_collection(),
        [
            {"$match": match},
            {
                "$group": {
                    "_id": "$data.model",
                    "calls": {"$sum": 1},
                    "input_tokens": {"$sum": "$data.input_tokens"},
                    "output_tokens": {"$sum": "$data.output_tokens"},
                    "cache_read_tokens": {"$sum": "$data.cache_read_tokens"},
                    "cache_write_tokens": {"$sum": "$data.cache_write_tokens"},
                }
            },
        ],
    )

    models: list[dict[str, Any]] = []
    total_cost = 0.0
    priced = True
    async for doc in cursor:
        model = str(doc["_id"])
        cost = estimate_cost(
            model,
            input_tokens=int(doc.get("input_tokens") or 0),
            output_tokens=int(doc.get("output_tokens") or 0),
            cache_read_tokens=int(doc.get("cache_read_tokens") or 0),
            cache_write_tokens=int(doc.get("cache_write_tokens") or 0),
        )
        if cost is None:
            # One unpriced model makes the *total* untrustworthy, so say so
            # rather than reporting a sum that silently excludes it.
            priced = False
        else:
            total_cost += cost
        models.append(
            {
                "model": model,
                "calls": int(doc.get("calls") or 0),
                "input_tokens": int(doc.get("input_tokens") or 0),
                "output_tokens": int(doc.get("output_tokens") or 0),
                "estimated_cost_usd": None if cost is None else round(cost, 4),
            }
        )

    models.sort(key=lambda row: row["calls"], reverse=True)
    return {
        "models": models,
        "estimated_cost_usd": round(total_cost, 4),
        "cost_is_complete": priced,
    }


async def event_totals(days: int, owner_id: str | None = None) -> dict[str, int]:
    """How many of each event type, for a quick "is anything happening" read."""
    match: dict[str, Any] = _window(days)
    if owner_id is not None:
        match["owner_id"] = owner_id
    cursor = await aggregate(
        get_events_collection(), [{"$match": match}, {"$group": {"_id": "$type", "n": {"$sum": 1}}}]
    )
    return {str(doc["_id"]): int(doc["n"]) async for doc in cursor}


async def active_owners(days: int) -> int:
    """Distinct users who did anything. The MAU number, when there are users."""
    values = await get_events_collection().distinct("owner_id", _window(days))
    return len([value for value in values if value])


async def feed_health(days: int) -> list[dict[str, Any]]:
    """Per-source refresh success rate, newest window first."""
    cursor = await aggregate(
        get_events_collection(),
        [
            {"$match": {"type": EventType.FEED_REFRESH.value, **_window(days)}},
            {
                "$group": {
                    "_id": "$data.feed",
                    "attempts": {"$sum": 1},
                    "ok": {"$sum": {"$cond": ["$data.ok", 1, 0]}},
                    "rows": {"$sum": "$data.rows"},
                }
            },
        ],
    )
    return sorted(
        [
            {
                "feed": str(doc["_id"]),
                "attempts": int(doc.get("attempts") or 0),
                "ok": int(doc.get("ok") or 0),
                "rows": int(doc.get("rows") or 0),
            }
            async for doc in cursor
        ],
        key=lambda row: row["feed"],
    )
