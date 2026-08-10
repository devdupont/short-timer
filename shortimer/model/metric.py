""""""

from dataclasses import dataclass
from enum import StrEnum


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
    #: The clock stopped. Paired with the above it gives a completion rate,
    #: which says something starts alone can't: whether the programming is the
    #: right size. It is also the moment an export to someone's own training
    #: log would fire — see `docs/exports.md`.
    WORKOUT_COMPLETED = "workout_completed"
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
