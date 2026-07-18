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

from pydantic import BaseModel, Field


class WorkoutMode(StrEnum):
    """Governs how the clock behaves for the workout as a whole."""

    FOR_TIME = "for_time"
    AMRAP = "amrap"
    EMOM = "emom"
    TABATA = "tabata"
    INTERVAL = "interval"
    CUSTOM = "custom"


class Movement(BaseModel):
    """A single exercise within a segment."""

    name: str
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
    mode: WorkoutMode

    time_cap_seconds: int | None = None
    rounds: int | None = None
    work_seconds: int | None = None
    rest_seconds: int | None = None
    rep_scheme: list[int] | None = None

    segments: list[WorkoutSegment] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkoutParseRequest(BaseModel):
    text: str
    name_hint: str | None = None


class WorkoutCreateRequest(BaseModel):
    workout: Workout


class LoginRequest(BaseModel):
    passcode: str
