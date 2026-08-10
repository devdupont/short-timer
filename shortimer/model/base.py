"""The Beanie document every other document in this package inherits from."""

import uuid

from beanie import Document
from pydantic import Field


class MongoDocument(Document):
    """A Beanie document keyed by a server-generated uuid4 hex string.

    Beanie's own `id` is `PydanticObjectId | None`; every document here uses
    a plain string instead — overriding the type is Beanie's documented way
    to customize it (mypy sees the override as narrowing rather than the
    intentional replacement it is at runtime, hence the `type: ignore`).
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)  # type: ignore[assignment]
