""""""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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
