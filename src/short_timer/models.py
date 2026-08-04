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


#: Human-facing name for each mode, mirroring MODE_LABELS in the web client.
#: The server needs them too: library search matches what a user types
#: ("AMRAP") rather than the value that gets stored ("amrap").
MODE_LABELS: dict[WorkoutMode, str] = {
    WorkoutMode.FOR_TIME: "For Time",
    WorkoutMode.AMRAP: "AMRAP",
    WorkoutMode.EMOM: "EMOM",
    WorkoutMode.TABATA: "Tabata",
    WorkoutMode.INTERVAL: "Interval",
    WorkoutMode.CUSTOM: "Custom",
}


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


class WorkoutSegment(BaseModel):
    """An ordered chunk of movements, e.g. one round of a chipper.

    `rounds` and `rep_scheme` let a segment nest its own repetition, which is
    what a workout like Murph needs: an outer for-time clock wrapping an inner
    "20 rounds of 5 pull-ups / 10 push-ups / 15 air squats" partition.
    """

    label: str | None = None
    rounds: int | None = None
    rep_scheme: list[int] | None = None
    movements: list[Movement] = Field(default_factory=list)


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


class WorkoutPage(BaseModel):
    """One page of library results, with what a client needs to page through."""

    items: list[Workout]
    #: How many workouts match the *current filters*, not how many the library
    #: holds — it's the page count of the view the user is actually looking at.
    total: int
    limit: int
    offset: int


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


class UserConfig(BaseModel):
    """Everything a user configures about their own account."""

    wodify_owner: WodifyOwnerConfig = Field(default_factory=WodifyOwnerConfig)
    wodify_member: WodifyMemberConfig = Field(default_factory=WodifyMemberConfig)


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
    """Config as the client sees it — credentials reduced to set/not-set."""

    wodify_owner: WodifyOwnerConfigView = Field(default_factory=WodifyOwnerConfigView)
    wodify_member: WodifyMemberConfigView = Field(default_factory=WodifyMemberConfigView)


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
