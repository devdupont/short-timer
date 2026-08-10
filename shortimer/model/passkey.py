""""""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


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
