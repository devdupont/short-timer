"""API tokens: long-lived, scoped credentials for the MCP server and other integrations."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, Field
from pymongo import IndexModel

from shortimer.model.base import MongoDocument
from shortimer.util.time import utcnow


class ApiTokenScope(StrEnum):
    """What an API token may do.

    Deliberately coarser than the HTTP surface: a token is a long-lived
    credential pasted into a config file, so the useful question is "can this
    integration change my library or only read it?", not a permission per
    route.
    """

    LIBRARY_READ = "library:read"
    LIBRARY_WRITE = "library:write"


class ApiToken(MongoDocument):
    """An issued token. The value itself is never part of this shape.

    The hash it's looked up by (`token_hash`) is deliberately not a field
    here — see `auth/api_tokens.py` — so that returning this model directly
    as a response (see `router/me.py`) can never leak it.
    """

    user_id: str
    name: str = Field(default="", max_length=200)
    scopes: list[ApiTokenScope] = Field(default_factory=list)
    #: First few characters of the token — not secret, and the only way a UI
    #: can let someone tell two tokens apart in order to revoke the right one.
    prefix: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None

    class Settings:
        """Beanie collection name and indexes."""

        name = "api_tokens"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel([("user_id", 1)]),
        ]


class ApiTokenCreateRequest(BaseModel):
    """A request to mint a new token."""

    name: str = Field(default="", max_length=200)
    scopes: list[ApiTokenScope] = Field(default_factory=lambda: [ApiTokenScope.LIBRARY_READ])
    #: Required even though the caller holds a session: this mints a
    #: credential that outlives it. Same reasoning as changing a password.
    current_password: str


class ApiTokenCreatedResponse(BaseModel):
    """The one and only time the token value is visible."""

    api_token: ApiToken
    token: str
