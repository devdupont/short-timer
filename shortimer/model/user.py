""""""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from shortimer.model.feed import FeedKind, FeedPref, default_feeds
from shortimer.model.gym import GymConnection, GymConnectionUpdate, GymConnectionView, GymProvider
from shortimer.model.status import AccountStatus, Role

# --- Users and their per-user configuration ---------------------------------
# Access to gym programming is per-person and per-gym: a gym owner has an API
# key for their own data, while a member can only read what their gym chose to
# publish. Neither is a property of the deployment, so this configuration
# belongs to a user rather than to the environment.


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
