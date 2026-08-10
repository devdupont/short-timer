""""""

from enum import StrEnum

from pydantic import BaseModel


class FeedKind(StrEnum):
    """A source of workouts the home page can show.

    Adding a source means adding a member here and teaching the home page to
    render it — not adding a tab. The set is closed on purpose: these are the
    integrations the server knows how to fetch, not free-form user input.
    """

    GYM = "gym"
    CROSSFIT = "crossfit"
    CONCEPT2 = "concept2"
    HYBRID = "hybrid"


#: Order a user sees before they reorder anything. Their own gym outranks a
#: public feed: if someone went to the trouble of connecting a box, that's the
#: programming they came for.
DEFAULT_FEED_ORDER: tuple[FeedKind, ...] = (
    FeedKind.GYM,
    FeedKind.CROSSFIT,
    FeedKind.CONCEPT2,
    FeedKind.HYBRID,
)

#: Feeds a brand-new account starts with switched on. Anything outside this set
#: exists but stays dark until the user asks for it — an erg feed is noise to
#: someone who doesn't own an erg. Kept separate from `DEFAULT_FEED_ORDER` so a
#: feed can have a sensible *position* without being on by default.
DEFAULT_ENABLED_FEEDS: frozenset[FeedKind] = frozenset({FeedKind.GYM, FeedKind.CROSSFIT})


class FeedPref(BaseModel):
    """Whether a feed appears on the home page.

    Distinct from the `enabled` flag on a `GymConnection`, which selects *which
    credential route* to fetch a gym with. This one is purely presentational:
    it decides what the home page renders, and position in the list is the
    display order.
    """

    kind: FeedKind
    enabled: bool = True


def default_feeds() -> list[FeedPref]:
    return [
        FeedPref(kind=kind, enabled=kind in DEFAULT_ENABLED_FEEDS) for kind in DEFAULT_FEED_ORDER
    ]


def normalize_feeds(feeds: list[FeedPref]) -> list[FeedPref]:
    """Drop duplicates and append any feed the stored list doesn't mention.

    Config written before a `FeedKind` existed simply won't list it, and a
    hand-edited document could repeat one. Rather than migrating records, every
    read passes through here — a new source shows up for existing users at its
    default position.

    It shows up *switched off*, regardless of `DEFAULT_ENABLED_FEEDS`. Shipping
    a feed shouldn't rearrange the home page of someone who already had one
    they were happy with; the defaults apply to accounts that have yet to
    express a preference, not to existing ones. A feed the caller does mention
    keeps whatever `enabled` it came with, so this never fights a real choice.
    """
    seen: dict[FeedKind, FeedPref] = {}
    for feed in feeds:
        seen.setdefault(feed.kind, feed)
    ordered = list(seen.values())
    ordered.extend(
        FeedPref(kind=kind, enabled=False) for kind in DEFAULT_FEED_ORDER if kind not in seen
    )
    return ordered
