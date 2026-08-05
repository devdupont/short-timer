"""Accounts and the per-user configuration hanging off them.

There's still exactly one way to authenticate — the shared passcode — but it
now resolves to a *user record* rather than a constant. That record is what
owns workouts and holds per-user integration settings, so adding real signup
later means adding a second way to arrive at a user id, not reworking storage.

The seeded default user's id is deliberately `DEFAULT_OWNER_ID`, matching what
`backfill_owner_ids` stamped on pre-tenancy rows. Every workout saved before
accounts existed therefore belongs to it, and nothing needs migrating.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status

from short_timer.auth import DEFAULT_OWNER_ID, current_owner
from short_timer.crypto import SecretBox, SecretStatus, encrypt, is_configured
from short_timer.db import get_users_collection
from short_timer.models import (
    AccountStatus,
    GymConnection,
    GymConnectionUpdate,
    GymConnectionView,
    GymProvider,
    MeResponse,
    Role,
    User,
    UserConfig,
    UserConfigUpdate,
    UserConfigView,
    normalize_feeds,
)

logger = logging.getLogger(__name__)


def normalize_email(email: str) -> str:
    """The stored form of an address.

    Case-folded and trimmed, so uniqueness is enforced by the index rather
    than by every caller remembering to lowercase first. Nothing more
    aggressive than that: stripping dots or `+tags` is a Gmail-specific rule,
    and applying it to every provider would silently merge addresses that
    really are different people.
    """
    return email.strip().lower()


def _to_document(user: User) -> dict[str, Any]:
    doc = user.model_dump(mode="json")
    doc["_id"] = doc.pop("id")
    return doc


def _from_document(doc: dict[str, Any]) -> User:
    data = dict(doc)
    data["id"] = data.pop("_id")
    return User(**data)


async def get_user(user_id: str) -> User | None:
    doc = await get_users_collection().find_one({"_id": user_id})
    return _from_document(doc) if doc is not None else None


async def get_user_by_email(email: str) -> User | None:
    doc = await get_users_collection().find_one({"email": normalize_email(email)})
    return _from_document(doc) if doc is not None else None


# --- Dependencies -----------------------------------------------------------
# These live here rather than in `auth.py` because they need user *records*,
# and `auth.py` deliberately knows only about session tokens. Keeping the
# import one-way (users -> auth) is what stops the two becoming a cycle.


async def current_user(owner_id: Annotated[str, Depends(current_owner)]) -> User:
    """The signed-in account.

    A valid session for an account that no longer exists is treated as no
    session at all: 401, not 404, because from the caller's point of view
    their credential simply doesn't authenticate anything any more.
    """
    user = await get_user(owner_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if user.status is not AccountStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    return user


def require_role(*allowed: Role) -> Callable[[User], Awaitable[User]]:
    """Dependency factory gating a route on the caller's role.

    Returns 404 rather than 403 on refusal. An endpoint you may not use
    shouldn't confirm that it exists — that's the existing reasoning behind
    the operator metrics gate, kept here so every privileged surface answers
    the same way.
    """
    permitted = frozenset(allowed)

    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in permitted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return user

    return dependency


async def ensure_default_user() -> bool:
    """Create the passcode user if it isn't there. True when one was created.

    Idempotent, and safe to race: two instances booting together both attempt
    the insert, and `$setOnInsert` means the loser doesn't clobber the winner.

    It is created with the *lowest* role on purpose. Everyone who knows the
    passcode signs in as this account, so making it an admin would hand the
    Anthropic bill to every visitor — exactly what the operator gate exists to
    prevent. Privileged access stays with `METRICS_ADMIN_USER_IDS` until real
    accounts land and there's someone specific to grant it to.
    """
    user = User(id=DEFAULT_OWNER_ID, display_name="Me", role=Role.USER)
    document = _to_document(user)
    document.pop("_id")
    result = await get_users_collection().update_one(
        {"_id": DEFAULT_OWNER_ID}, {"$setOnInsert": document}, upsert=True
    )
    created = result.upserted_id is not None
    if created:
        logger.info("Seeded the default user account.")
    return created


# --- Reading config out to a client -----------------------------------------


def _status(box: SecretBox | None) -> SecretStatus:
    """Describe a stored credential without decrypting it."""
    if box is None:
        return SecretStatus(is_set=False)
    return SecretStatus(is_set=True, masked=box.masked())


def _connection_view(connection: GymConnection) -> GymConnectionView:
    return GymConnectionView(
        provider=connection.provider,
        credential=_status(connection.credential),
        location=connection.location,
        program=connection.program,
        enabled=connection.enabled,
    )


def to_view(config: UserConfig) -> UserConfigView:
    """Config with every credential reduced to set/not-set plus a mask."""
    return UserConfigView(
        gyms=[_connection_view(connection) for connection in config.gyms],
        # Normalized on the way out so a record written before a feed kind
        # existed still reports a complete list.
        feeds=normalize_feeds(config.feeds),
    )


def to_me(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        role=user.role,
        display_name=user.display_name,
        config=to_view(user.config),
        secrets_available=is_configured(),
    )


# --- Applying a requested change --------------------------------------------


def _apply_secret(current: SecretBox | None, submitted: str | None) -> SecretBox | None:
    """Resolve the three-way credential update: keep, clear, or replace.

    Absent (None) keeps what's stored, so a client can toggle `enabled` or fix
    a typo'd location without holding the credential to send back. An explicit
    empty string clears it. Anything else is encrypted and replaces it.
    """
    if submitted is None:
        return current
    if not submitted.strip():
        return None
    return encrypt(submitted.strip())


def _apply_text(current: str | None, submitted: str | None) -> str | None:
    """Same keep/clear/replace rule as credentials, for plain fields."""
    if submitted is None:
        return current
    return submitted.strip() or None


def _apply_connection(
    config: UserConfig, provider: GymProvider, update: GymConnectionUpdate
) -> None:
    """Merge one provider's change into `config.gyms`, in place.

    A connection is created on first write and *removed* once it holds nothing
    worth keeping — clearing the credential on a connection the user has also
    switched off is how you disconnect a gym, and leaving an empty husk behind
    would make the settings screen claim a connection that can never fetch.
    """
    current = config.connection(provider) or GymConnection(provider=provider)
    merged = GymConnection(
        provider=provider,
        credential=_apply_secret(current.credential, update.credential),
        location=_apply_text(current.location, update.location),
        program=_apply_text(current.program, update.program),
        enabled=current.enabled if update.enabled is None else update.enabled,
    )
    config.gyms = [c for c in config.gyms if c.provider != provider]
    if merged.credential is not None or merged.location or merged.program:
        config.gyms.append(merged)


async def update_config(user: User, update: UserConfigUpdate) -> User:
    """Merge a partial config change into the user's stored config."""
    config = user.config.model_copy(deep=True)

    if update.gyms is not None:
        for provider, connection_update in update.gyms.items():
            _apply_connection(config, provider, connection_update)

    if update.feeds is not None:
        config.feeds = normalize_feeds(update.feeds)

    updated = user.model_copy(update={"config": config, "updated_at": datetime.now(UTC)})
    await get_users_collection().update_one(
        {"_id": updated.id},
        {
            "$set": {
                "config": config.model_dump(mode="json"),
                "updated_at": updated.updated_at.isoformat(),
            }
        },
    )
    return updated
