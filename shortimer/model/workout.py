""""""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from shortimer.util.dedup import source_hash


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
    def _infer_is_rest(self) -> Self:
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
    def _populate_source_hash(self) -> Self:
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
