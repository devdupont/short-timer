"""Domain model for workouts and the timers that drive them.

A `Workout` describes both *what to do* (movements, rep schemes) and *how the
clock should run* (for time, AMRAP, EMOM, tabata, interval, or a plain rest
day / custom note). The same shape is produced whether a workout was typed in
by hand, pasted from another source and parsed by the LLM, or pulled from the
scraped benchmark library, so the frontend timer engine only needs to
understand one schema.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from short_timer.crypto import SecretBox, SecretStatus
from short_timer.dedup import source_hash


class WorkoutMode(StrEnum):
    """Governs how the clock behaves for the workout as a whole."""

    FOR_TIME = "for_time"
    AMRAP = "amrap"
    EMOM = "emom"
    TABATA = "tabata"
    INTERVAL = "interval"
    CUSTOM = "custom"


class Movement(BaseModel):
    """A single exercise within a segment.

    `name` is optional because some interval workouts don't name a movement
    at all (e.g. "30 seconds on, 15 seconds rest" with no exercise
    specified) — the clock still needs a work/rest leg to run against.
    """

    name: str | None = None
    reps: int | None = None
    distance: str | None = None
    calories: int | None = None
    load: str | None = None
    notes: str | None = None


#: Text that means "this leg is recovery, not work". Deliberately small and
#: matched whole: `is_rest` is inferred only from a segment that says nothing
#: *but* one of these, so "16 Renegade Rows" is never mistaken for a breather.
_REST_WORDS = frozenset(
    {
        "rest",
        "rest period",
        "recovery",
        "active recovery",
        "complete rest",
        "full rest",
    }
)


def _is_rest_word(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().strip(".!:").casefold() in _REST_WORDS


def _is_rest_movement(movement: Movement) -> bool:
    """A movement that's really a breather: named rest, with nothing to do."""
    if movement.reps or movement.calories or movement.distance or movement.load:
        return False
    return _is_rest_word(movement.name)


class WorkoutSegment(BaseModel):
    """An ordered chunk of movements, e.g. one round of a chipper.

    `rounds` and `rep_scheme` let a segment nest its own repetition, which is
    what a workout like Murph needs: an outer for-time clock wrapping an inner
    "20 rounds of 5 pull-ups / 10 push-ups / 15 air squats" partition.

    `work_seconds` and `rest_seconds` extend that same idea to the clock: they
    let one leg run for a different length than its neighbours. A "5/4/3/2/1
    minutes with 2 minutes rest" ladder is five segments with five different
    work durations, which the workout-level scalars alone can't express. Both
    fall back to the workout-level values, so a uniform "6 x 3 minutes" leaves
    them unset and reads exactly as it did before these existed.

    `is_rest` marks a leg that *is* the recovery — an EMOM whose fifth minute
    reads "Rest". That's different from `rest_seconds`, which is recovery
    appended to a leg of work; here the leg's whole duration is the rest, and
    the clock should say so rather than announce a movement nobody performs.
    """

    label: str | None = None
    rounds: int | None = None
    rep_scheme: list[int] | None = None
    work_seconds: int | None = None
    rest_seconds: int | None = None
    is_rest: bool = False
    movements: list[Movement] = Field(default_factory=list)

    @model_validator(mode="after")
    def _infer_is_rest(self) -> WorkoutSegment:
        """Treat a segment that only says "Rest" as a rest leg.

        The flag is what the timer reads, but a rest minute has always arrived
        as a movement named "Rest" — from the parser, from the MCP tool, and in
        every workout already saved. Inferring here means those keep working
        without a migration or a re-parse, and it only ever turns the flag
        *on*: a segment with real work in it is never reinterpreted.
        """
        if self.is_rest:
            return self
        if self.movements:
            self.is_rest = all(_is_rest_movement(m) for m in self.movements)
        else:
            self.is_rest = _is_rest_word(self.label)
        return self


class Workout(BaseModel):
    """A complete, timer-ready workout."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    description: str | None = None
    category: str | None = None
    source_text: str | None = None
    # Normalized hash of `source_text`, used to dedupe parses and skip
    # redundant LLM calls. Auto-derived below when source_text is present.
    source_hash: str | None = None
    # Who this workout belongs to. Server-assigned from the session on every
    # write — never trusted from the request body.
    owner_id: str | None = None
    mode: WorkoutMode

    time_cap_seconds: int | None = None
    rounds: int | None = None
    work_seconds: int | None = None
    rest_seconds: int | None = None
    rep_scheme: list[int] | None = None

    segments: list[WorkoutSegment] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _populate_source_hash(self) -> Workout:
        if self.source_text and not self.source_hash:
            self.source_hash = source_hash(self.source_text)
        return self


class WorkoutParseRequest(BaseModel):
    # Bounded because this text is sent to the model — an unbounded paste is a
    # direct route to a large token bill. Real workouts are a few hundred
    # characters; crossfit.com's longest daily entries are ~2k.
    text: str = Field(min_length=1, max_length=20_000)
    name_hint: str | None = Field(default=None, max_length=200)


class WorkoutCreateRequest(BaseModel):
    workout: Workout


class SeedResponse(BaseModel):
    """Outcome of seeding the benchmark workouts into the library."""

    added: int
    skipped: int


class LoginRequest(BaseModel):
    passcode: str


# --- Users and their per-user configuration ---------------------------------
# Access to gym programming is per-person and per-gym: a gym owner has an API
# key for their own data, while a member can only read what their gym chose to
# publish. Neither is a property of the deployment, so this configuration
# belongs to a user rather than to the environment.


class WodifyOwnerConfig(BaseModel):
    """Wodify Program API access, for someone who runs the gym.

    The API key is minted by a gym admin in Wodify and grants access to that
    gym's programming, so it's a credential and never leaves the server in the
    clear — see `UserConfigView` for what a client actually receives.
    """

    api_key: SecretBox | None = None
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool = False

    def is_usable(self) -> bool:
        """Whether this is complete enough to fetch with."""
        return bool(self.enabled and self.api_key and self.location and self.program)


class WodifyMemberConfig(BaseModel):
    """Wodify public whiteboard access, for a member of the gym.

    The whiteboard key only works if the gym enabled public publishing, and it
    appears in a URL the gym hands out — so it's far less sensitive than an API
    key. It's still encrypted: it identifies the user's gym, and treating every
    third-party credential the same way is one less exception to reason about.
    """

    whiteboard_key: SecretBox | None = None
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool = False

    def is_usable(self) -> bool:
        return bool(self.enabled and self.whiteboard_key)


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

    Distinct from the `enabled` flag on a Wodify config, which selects *which
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


class UserConfig(BaseModel):
    """Everything a user configures about their own account."""

    wodify_owner: WodifyOwnerConfig = Field(default_factory=WodifyOwnerConfig)
    wodify_member: WodifyMemberConfig = Field(default_factory=WodifyMemberConfig)
    feeds: list[FeedPref] = Field(default_factory=default_feeds)


class User(BaseModel):
    """An account. One per person; owns their workouts via `owner_id`."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    display_name: str = Field(default="", max_length=200)
    config: UserConfig = Field(default_factory=UserConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- Wire shapes ------------------------------------------------------------
# Credentials travel in one direction only: a client may send a new value, and
# reads report whether one is stored. These separate models are what enforce
# that, rather than remembering to strip fields at each call site.


class WodifyOwnerConfigView(BaseModel):
    api_key: SecretStatus = Field(default_factory=SecretStatus)
    location: str | None = None
    program: str | None = None
    enabled: bool = False


class WodifyMemberConfigView(BaseModel):
    whiteboard_key: SecretStatus = Field(default_factory=SecretStatus)
    location: str | None = None
    program: str | None = None
    enabled: bool = False


class UserConfigView(BaseModel):
    """Config as the client sees it — credentials reduced to set/not-set.

    `feeds` carries no secret, so it crosses the boundary as-is rather than
    getting a parallel view model that would only ever copy fields across.
    """

    wodify_owner: WodifyOwnerConfigView = Field(default_factory=WodifyOwnerConfigView)
    wodify_member: WodifyMemberConfigView = Field(default_factory=WodifyMemberConfigView)
    feeds: list[FeedPref] = Field(default_factory=default_feeds)


class MeResponse(BaseModel):
    id: str
    display_name: str
    config: UserConfigView
    #: False when the deployment has no encryption keys, so the UI can explain
    #: why saving a credential won't work instead of failing on submit.
    secrets_available: bool = True


class WodifyOwnerConfigUpdate(BaseModel):
    """A requested change. Every field is optional: omitted means "leave it".

    `api_key` distinguishes three cases — absent leaves the stored key alone
    (so the UI can save other fields without re-entering it), empty string
    clears it, and a value replaces it.
    """

    api_key: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class WodifyMemberConfigUpdate(BaseModel):
    whiteboard_key: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class UserConfigUpdate(BaseModel):
    wodify_owner: WodifyOwnerConfigUpdate | None = None
    wodify_member: WodifyMemberConfigUpdate | None = None
    #: Replaced wholesale rather than merged, because position *is* the display
    #: order — there's no unambiguous way to merge one entry into an ordered
    #: list. Absent still means "leave it alone". Bounded by the number of
    #: kinds that exist, since anything longer is duplicates or junk.
    feeds: list[FeedPref] | None = Field(default=None, max_length=len(FeedKind))
