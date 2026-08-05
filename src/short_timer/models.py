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
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, EmailStr, Field, model_validator

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


class IntervalClock(StrEnum):
    """Which way the clock runs *inside* one leg of an interval workout.

    Counting down is the default and what an EMOM wants: the number on the
    wall is how long you have left to finish the minute's work.

    Counting up is for interval work that's scored by *when each set
    finished* — "Every 3:00 x 5 sets ... score = slowest set time". The window
    is only the container; what an athlete needs to read off the wall is their
    own split, and they each finish at a different moment, so a shared
    countdown can't tell them. Same plan, same legs, same rest — only the
    number the athlete reads changes.
    """

    COUNT_DOWN = "count_down"
    COUNT_UP = "count_up"


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
    #: Which way the clock runs within a leg. Only the interval modes have legs
    #: to run either way — for_time and amrap already count up against a cap —
    #: so this is read for emom/tabata/interval and ignored elsewhere.
    interval_clock: IntervalClock = IntervalClock.COUNT_DOWN

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


class WorkoutCompletedRequest(BaseModel):
    """How long the clock ran, reported when a session ends.

    Bounded because it comes from a browser and is only ever read as a
    statistic: a clock left running overnight would drag an average around
    with no way to tell it from a real session. Twelve hours is well past any
    workout and short of anything that could be a stuck tab.
    """

    elapsed_seconds: float = Field(ge=0, le=12 * 60 * 60)


class WorkoutPage(BaseModel):
    """One page of a library listing.

    `total` counts everything matching the query, not just this page, so the
    client can render "showing 1-25 of 120" and know whether a next page
    exists without asking for it.
    """

    items: list[Workout]
    total: int
    limit: int
    offset: int


class SeedResponse(BaseModel):
    """Outcome of seeding the benchmark workouts into the library."""

    added: int
    skipped: int


class Role(StrEnum):
    """What a user may do *globally*.

    This axis answers "what may you see across the whole deployment?" and
    nothing else. It is deliberately not the place a gym owner goes: being
    privileged over your own gym's data is a question of *scope* — which
    records — not of rank, and folding the two together produces a role enum
    that has to grow a new member every time a new kind of boundary appears.
    Gym scoping belongs with the plan/tier work; see `docs/roadmap.md`.
    """

    #: Everyone. Sees their own data and nothing else.
    USER = "user"
    #: Support and ops: may read the operator metrics, may not administer
    #: accounts. Exists so the privileged check can't collapse to `is_admin`.
    STAFF = "staff"
    #: The operator. Everything, including invites and other accounts.
    ADMIN = "admin"


#: Roles allowed to read the operator metrics — global spend, every user's
#: activity. Named here rather than spelled out at the call site so that
#: adding a privileged surface is a change in one place.
OPERATOR_ROLES = frozenset({Role.STAFF, Role.ADMIN})


class AccountStatus(StrEnum):
    """Whether an account may be used at all, separate from what it may do."""

    ACTIVE = "active"
    #: Sign-in refused, data retained. What a ban or a voluntary pause looks
    #: like; deletion is a different operation.
    DISABLED = "disabled"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    """Redeeming an invite into an account.

    `password` has a floor but no ceiling worth enforcing beyond a sanity
    bound: Argon2 has no input length limit (this is the bcrypt trap the
    algorithm choice avoids), so a long passphrase is simply a better password.
    """

    invite_token: str
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    display_name: str = Field(default="", max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    #: Required even though the caller is already authenticated. A session is
    #: long-lived here, so possession of one shouldn't be enough to lock the
    #: real owner out of their own account.
    current_password: str
    new_password: str = Field(min_length=1, max_length=1024)


class VerifyEmailRequest(BaseModel):
    token: str


class InviteCreateRequest(BaseModel):
    #: Omit for an open code that anyone holding it may redeem. Naming an
    #: address both restricts redemption and lets us skip the confirmation
    #: email, since delivery already proved control of the mailbox.
    email: EmailStr | None = None
    role: Role = Role.USER


class Invite(BaseModel):
    """A signup invitation. The token itself is never part of this shape."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    email: str | None = None
    role: Role = Role.USER
    created_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    redeemed_at: datetime | None = None
    redeemed_by: str | None = None


class InviteCreatedResponse(BaseModel):
    """The one and only time the invite token is visible."""

    invite: Invite
    token: str
    #: Ready to paste to someone when no email was sent.
    link: str
    #: False when an address was named but the mail couldn't be delivered, so
    #: the admin knows to pass the link along by hand rather than assuming.
    emailed: bool


class Passkey(BaseModel):
    """A registered WebAuthn credential. The public key isn't part of this shape.

    `id` is the base64url credential id the authenticator produced — not one we
    chose, because it's what the browser sends back to identify the credential.
    """

    id: str
    user_id: str
    nickname: str = Field(default="", max_length=200)
    #: The clone detector. Many passkeys report a constant 0, which means "not
    #: supported" rather than "cloned".
    sign_count: int = 0
    aaguid: str = ""
    #: Whether the credential syncs to a provider's cloud. A device-bound
    #: passkey is lost with the device, which is what makes "register a second
    #: one" concrete advice rather than nagging.
    backed_up: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None


class PasskeyRegisterRequest(BaseModel):
    """The attestation coming back from `navigator.credentials.create()`."""

    challenge_handle: str
    credential: dict[str, Any]
    nickname: str = Field(default="", max_length=200)


class PasskeyLoginRequest(BaseModel):
    """The assertion coming back from `navigator.credentials.get()`."""

    challenge_handle: str
    credential: dict[str, Any]


class PasskeyChallengeResponse(BaseModel):
    """What the browser needs to run a ceremony.

    `options` is JSON built by py_webauthn rather than a model of ours: it's
    passed straight to the WebAuthn API, and re-describing it here would be a
    second place for the spec's shape to drift.
    """

    challenge_handle: str
    options: dict[str, Any]


class ApiTokenScope(StrEnum):
    """What an API token may do.

    Deliberately coarser than the HTTP surface: a token is a long-lived
    credential pasted into a config file, so the useful question is "can this
    integration change my library or only read it?", not a permission per
    route.
    """

    LIBRARY_READ = "library:read"
    LIBRARY_WRITE = "library:write"


class ApiToken(BaseModel):
    """An issued token. The value itself is never part of this shape."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str
    name: str = Field(default="", max_length=200)
    scopes: list[ApiTokenScope] = Field(default_factory=list)
    #: First few characters of the token — not secret, and the only way a UI
    #: can let someone tell two tokens apart in order to revoke the right one.
    prefix: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None


class ApiTokenCreateRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    scopes: list[ApiTokenScope] = Field(default_factory=lambda: [ApiTokenScope.LIBRARY_READ])
    #: Required even though the caller holds a session: this mints a
    #: credential that outlives it. Same reasoning as changing a password.
    current_password: str


class ApiTokenCreatedResponse(BaseModel):
    """The one and only time the token value is visible."""

    api_token: ApiToken
    token: str


class InviteCheckResponse(BaseModel):
    """What the register screen needs before asking for a password."""

    valid: bool
    #: Pre-fills and locks the email field for an address-bound invite.
    email: str | None = None
    reason: str | None = None


# --- Users and their per-user configuration ---------------------------------
# Access to gym programming is per-person and per-gym: a gym owner has an API
# key for their own data, while a member can only read what their gym chose to
# publish. Neither is a property of the deployment, so this configuration
# belongs to a user rather than to the environment.


# --- Gym providers -----------------------------------------------------------
# A gym's programming lives on whichever platform the gym pays for, reached by
# a route that differs by whether you run the gym or attend it. Those two axes
# multiply, so each *combination* is a provider rather than trying to model
# platform and role separately — "the SugarWOD owner route" is the unit that
# has a URL, a credential and a response shape.


class GymProvider(StrEnum):
    """One way of reaching one gym platform.

    Closed on purpose, like `FeedKind`: these are the routes the server knows
    how to fetch, not free-form user input. `gym_providers.py` holds the
    human-facing metadata and the fetch dispatch for each member here.
    """

    WODIFY_MEMBER = "wodify_member"
    WODIFY_OWNER = "wodify_owner"
    SUGARWOD_OWNER = "sugarwod_owner"


#: Which provider is tried first when a user has configured more than one.
#: Member routes outrank owner routes: someone who is both an admin and an
#: athlete sees the same gym either way, and a public whiteboard costs no API
#: quota. Beyond that it's the order providers shipped in, which is arbitrary
#: but stable — and a user who wants a different answer switches the other
#: connection off.
PROVIDER_PRIORITY: tuple[GymProvider, ...] = (
    GymProvider.WODIFY_MEMBER,
    GymProvider.WODIFY_OWNER,
    GymProvider.SUGARWOD_OWNER,
)


class GymConnection(BaseModel):
    """One stored gym credential, plus the settings that qualify it.

    `location` and `program` are deliberately generic rather than named per
    platform: Wodify calls them location and program, SugarWOD calls the second
    one a track, and inventing a field per platform would put the registry's
    job (labelling) into the storage schema. What each one *means* is declared
    in `gym_providers.py` and rendered from there.
    """

    provider: GymProvider
    credential: SecretBox | None = None
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool = False

    def is_usable(self) -> bool:
        """Whether this is complete enough to fetch with.

        Only the credential is required here. Whether a provider *also* needs
        a location or program is a property of that provider, so the check
        lives with it — see `GymProviderSpec.is_usable`.
        """
        return bool(self.enabled and self.credential)


class GymWod(BaseModel):
    """One day's workout from a gym, whichever platform it came from.

    Mirrors `crossfit.Wod` rather than reusing it: the two intakes share a
    shape today but not a lifecycle — crossfit.com has rest days and a public
    permalink per day, a gym has neither — and coupling them would mean every
    change to one route rippling into the other.

    Lives here rather than beside a client because every provider produces
    one; `provider` records which did, so a card can say "View on SugarWOD"
    without the frontend having to infer it from the URL.
    """

    date: date
    title: str
    text: str
    #: A "see it at the source" pointer, or empty when the platform has no
    #: page we can link a member to without leaking their credential. The UI
    #: drops the link rather than rendering a dead one.
    url: str = ""
    provider: GymProvider


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


class UserConfig(BaseModel):
    """Everything a user configures about their own account."""

    gyms: list[GymConnection] = Field(default_factory=list)
    feeds: list[FeedPref] = Field(default_factory=default_feeds)

    def connection(self, provider: GymProvider) -> GymConnection | None:
        return next((c for c in self.gyms if c.provider == provider), None)


class User(BaseModel):
    """An account. One per person; owns their workouts via `owner_id`."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    #: Normalised to lowercase on write (see `users.normalize_email`) so that
    #: uniqueness is a property of the index rather than of every caller
    #: remembering to fold case. Optional only while the shared passcode still
    #: exists; real accounts always carry one.
    email: str | None = None
    email_verified: bool = False
    #: Argon2id PHC string, or None for an account that signs in by other
    #: means (a passkey) and has never set a password.
    password_hash: str | None = None
    role: Role = Role.USER
    status: AccountStatus = AccountStatus.ACTIVE
    display_name: str = Field(default="", max_length=200)
    config: UserConfig = Field(default_factory=UserConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- Wire shapes ------------------------------------------------------------
# Credentials travel in one direction only: a client may send a new value, and
# reads report whether one is stored. These separate models are what enforce
# that, rather than remembering to strip fields at each call site.


class GymConnectionView(BaseModel):
    """One stored connection, with its credential reduced to set/not-set."""

    provider: GymProvider
    credential: SecretStatus = Field(default_factory=SecretStatus)
    location: str | None = None
    program: str | None = None
    enabled: bool = False


class UserConfigView(BaseModel):
    """Config as the client sees it — credentials reduced to set/not-set.

    `feeds` carries no secret, so it crosses the boundary as-is rather than
    getting a parallel view model that would only ever copy fields across.
    """

    gyms: list[GymConnectionView] = Field(default_factory=list)
    feeds: list[FeedPref] = Field(default_factory=default_feeds)


class MeResponse(BaseModel):
    id: str
    #: None only for the shared-passcode account, which has no email.
    email: str | None = None
    email_verified: bool = False
    role: Role = Role.USER
    display_name: str
    config: UserConfigView
    #: False when the deployment has no encryption keys, so the UI can explain
    #: why saving a credential won't work instead of failing on submit.
    secrets_available: bool = True


class GymConnectionUpdate(BaseModel):
    """A requested change to one gym connection.

    `credential` is three-way: absent leaves the stored value alone (so the UI
    can fix a typo'd location without re-entering the key), an empty string
    clears it, and anything else replaces it.
    """

    credential: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class UserConfigUpdate(BaseModel):
    #: Keyed by provider rather than a list, because a list would face the
    #: same unmergeable-position problem `feeds` has — and unlike feeds, order
    #: here isn't user-facing, so there's nothing to be gained by paying it.
    #: Only the providers named are touched; the rest are left alone.
    gyms: dict[GymProvider, GymConnectionUpdate] | None = None
    #: Replaced wholesale rather than merged, because position *is* the display
    #: order — there's no unambiguous way to merge one entry into an ordered
    #: list. Absent still means "leave it alone". Bounded by the number of
    #: kinds that exist, since anything longer is duplicates or junk.
    feeds: list[FeedPref] | None = Field(default=None, max_length=len(FeedKind))
