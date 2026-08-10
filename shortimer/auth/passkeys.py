"""Passkeys — the server half of the WebAuthn ceremonies.

A passkey replaces the *password*, not the account. The account still has an
email address underneath it, because a passkey that only exists on a lost phone
needs a way back in, and that way is the password-reset flow.

Both ceremonies are two round trips. We issue a challenge; the authenticator
signs it; we verify the signature against the stored public key. The challenge
is the anti-replay measure, so it has to be *ours*, single-use and short-lived —
which is why it goes in a collection rather than being handed to the client to
give back.

## The RP ID is permanent

`rp_id` is hashed into the credential when it's created and can never be
changed afterwards. It must be `shortimer.com`, the apex — a credential
registered at the apex works from any subdomain, while one registered at
`api.shortimer.com` could never be used at the apex or a sibling. Getting this
wrong is unrecoverable short of a `.well-known/webauthn` Related Origins file,
so it is configuration with a loud comment rather than something derived.

Note that the RP ID and the *origin* differ here on purpose: the browser is at
`https://shortimer.com` while this code runs on `api.shortimer.com`. That's
legal because the RP ID may be a registrable suffix of the origin.

## Discoverable credentials

Registration asks for a resident key, and authentication sends an empty
`allow_credentials`. Together those are what make "sign in with a passkey" work
without typing an email first: the browser offers whichever passkey it holds
for this site, and the credential itself tells us who its owner is.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import webauthn as webauthn  # re-exported: tests monkeypatch passkeys.webauthn
from webauthn.helpers import options_to_json
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from shortimer.auth.tokens import hash_token, new_token
from shortimer.cache.db import get_credentials_collection, get_webauthn_challenges_collection
from shortimer.config import get_settings
from shortimer.model.passkey import Passkey
from shortimer.model.user import User

logger = logging.getLogger(__name__)

#: How long a challenge stays usable. Long enough to pick a finger up and touch
#: a sensor, short enough that a captured one is worthless.
_CHALLENGE_TTL = timedelta(minutes=5)


class PasskeyError(RuntimeError):
    """A ceremony that could not be completed."""


# --- Challenges --------------------------------------------------------------


async def _issue_challenge(challenge: bytes, *, user_id: str | None) -> str:
    """Store a challenge and return the handle the client sends back.

    The handle is a random token rather than the challenge itself, so a client
    can't choose the challenge it will later be asked to have signed.
    """
    handle = new_token()
    await get_webauthn_challenges_collection().insert_one(
        {
            "_id": hash_token(handle),
            "challenge": challenge,
            # None for a login ceremony: we don't know who's signing in yet,
            # which is the whole point of a discoverable credential.
            "user_id": user_id,
            "expires_at": datetime.now(UTC) + _CHALLENGE_TTL,
        }
    )
    return handle


async def _spend_challenge(handle: str) -> tuple[bytes, str | None]:
    """Consume a challenge, or raise. Single use, and expiry checked here.

    Deleted on read for the same reason email tokens are: a challenge that can
    be presented twice is a replay waiting to happen, and the TTL index sweeps
    too slowly to be the check.
    """
    doc = await get_webauthn_challenges_collection().find_one_and_delete(
        {"_id": hash_token(handle)}
    )
    if doc is None:
        raise PasskeyError("That passkey attempt has expired. Try again.")

    expires_at = doc.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) >= expires_at:
            raise PasskeyError("That passkey attempt has expired. Try again.")

    challenge = doc.get("challenge")
    if not isinstance(challenge, bytes):
        raise PasskeyError("That passkey attempt has expired. Try again.")
    return challenge, doc.get("user_id")


# --- Stored credentials ------------------------------------------------------


def _from_document(doc: dict[str, Any]) -> Passkey:
    data = dict(doc)
    data["id"] = data.pop("_id")
    data.pop("public_key", None)
    return Passkey(**data)


async def list_passkeys(user_id: str) -> list[Passkey]:
    cursor = get_credentials_collection().find({"user_id": user_id}).sort("created_at", -1)
    return [_from_document(doc) async for doc in cursor]


async def count_passkeys(user_id: str) -> int:
    return int(await get_credentials_collection().count_documents({"user_id": user_id}))


async def delete_passkey(user_id: str, credential_id: str) -> bool:
    """Remove one of this user's passkeys.

    Scoped to the owner in the query, so there's no path where a mistake reads
    one user's credential id and deletes another's.
    """
    result = await get_credentials_collection().delete_one(
        {"_id": credential_id, "user_id": user_id}
    )
    return result.deleted_count == 1


# --- Registration ------------------------------------------------------------


async def start_registration(user: User) -> tuple[str, str]:
    """Options for `navigator.credentials.create()`, plus a challenge handle."""
    settings = get_settings()

    # Offering the ones already registered stops a device silently creating a
    # second credential for itself, which would show up as a duplicate row the
    # user can't tell apart.
    existing = [
        PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(passkey.id))
        async for passkey in _iter_credentials(user.id)
    ]

    options = webauthn.generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        # A stable per-account handle. The user id is opaque to the
        # authenticator; the name and display name are what a passkey picker
        # shows, so they have to be human-readable.
        user_id=user.id.encode(),
        user_name=user.email or user.id,
        user_display_name=user.display_name or user.email or "shortimer",
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Discoverable, so signing in needs no email typed first.
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    handle = await _issue_challenge(options.challenge, user_id=user.id)
    return handle, options_to_json(options)


async def _iter_credentials(user_id: str) -> AsyncIterator[Passkey]:
    async for doc in get_credentials_collection().find({"user_id": user_id}):
        yield _from_document(doc)


async def finish_registration(
    user: User, handle: str, credential: dict[str, Any], nickname: str
) -> Passkey:
    """Verify an attestation and store the credential."""
    challenge, challenge_user = await _spend_challenge(handle)
    if challenge_user != user.id:
        raise PasskeyError("That passkey attempt belongs to a different account.")

    settings = get_settings()
    try:
        verified = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
        )
    except InvalidRegistrationResponse as exc:
        logger.warning("Passkey registration failed verification: %s", exc)
        raise PasskeyError("That passkey could not be registered.") from exc

    credential_id = _b64(verified.credential_id)
    passkey = Passkey(
        id=credential_id,
        user_id=user.id,
        nickname=nickname.strip() or "Passkey",
        sign_count=verified.sign_count,
        aaguid=str(verified.aaguid),
        # Whether the credential syncs to a provider's cloud. Worth surfacing:
        # a device-bound passkey is lost with the device, which is what makes
        # "add a second one" advice concrete rather than nagging.
        backed_up=bool(verified.credential_backed_up),
        created_at=datetime.now(UTC),
    )

    document = passkey.model_dump(mode="json")
    document["_id"] = document.pop("id")
    document["public_key"] = verified.credential_public_key
    document["created_at"] = passkey.created_at
    await get_credentials_collection().insert_one(document)
    logger.info("Registered passkey %s for %s.", credential_id[:12], user.id)
    return passkey


# --- Authentication ----------------------------------------------------------


async def start_authentication() -> tuple[str, str]:
    """Options for `navigator.credentials.get()`, plus a challenge handle.

    `allow_credentials` is deliberately empty: the browser offers whichever
    discoverable passkey it holds for this site, so the user doesn't have to
    say who they are before proving it. It also means this endpoint reveals
    nothing about which accounts exist.
    """
    options = webauthn.generate_authentication_options(
        rp_id=get_settings().webauthn_rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    handle = await _issue_challenge(options.challenge, user_id=None)
    return handle, options_to_json(options)


async def finish_authentication(handle: str, credential: dict[str, Any]) -> str:
    """Verify an assertion and return the user it authenticates."""
    challenge, _ = await _spend_challenge(handle)

    raw_id = credential.get("rawId") or credential.get("id")
    if not isinstance(raw_id, str):
        raise PasskeyError("That passkey could not be verified.")

    doc = await get_credentials_collection().find_one({"_id": raw_id})
    if doc is None:
        raise PasskeyError("That passkey is not registered.")

    settings = get_settings()
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
            credential_public_key=doc["public_key"],
            credential_current_sign_count=int(doc.get("sign_count", 0)),
        )
    except InvalidAuthenticationResponse as exc:
        logger.warning("Passkey authentication failed verification: %s", exc)
        raise PasskeyError("That passkey could not be verified.") from exc

    # The counter is the clone detector: an authenticator that increments it
    # must never go backwards. py_webauthn enforces that above; storing the new
    # value is what keeps the check meaningful next time. Many passkeys report
    # a constant 0, and that's fine — it means "not supported", not "cloned".
    await get_credentials_collection().update_one(
        {"_id": raw_id},
        {"$set": {"sign_count": verified.new_sign_count, "last_used_at": datetime.now(UTC)}},
    )

    user_id = doc.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise PasskeyError("That passkey is not registered.")
    return user_id


def _b64(raw: bytes) -> str:
    """base64url, unpadded — the form WebAuthn uses for credential ids."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
