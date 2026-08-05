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
from datetime import UTC, datetime
from typing import Any

from short_timer.auth import DEFAULT_OWNER_ID
from short_timer.crypto import SecretBox, SecretStatus, encrypt, is_configured
from short_timer.db import get_users_collection
from short_timer.models import (
    GymConnection,
    GymConnectionUpdate,
    GymConnectionView,
    GymProvider,
    MeResponse,
    User,
    UserConfig,
    UserConfigUpdate,
    UserConfigView,
    WodifyMemberConfigView,
    WodifyOwnerConfigView,
    normalize_feeds,
)

logger = logging.getLogger(__name__)


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


async def ensure_default_user() -> bool:
    """Create the passcode user if it isn't there. True when one was created.

    Idempotent, and safe to race: two instances booting together both attempt
    the insert, and `$setOnInsert` means the loser doesn't clobber the winner.
    """
    user = User(id=DEFAULT_OWNER_ID, display_name="Me")
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
    gyms = [_connection_view(connection) for connection in config.gyms]
    member = config.connection(GymProvider.WODIFY_MEMBER)
    owner = config.connection(GymProvider.WODIFY_OWNER)
    return UserConfigView(
        gyms=gyms,
        # Normalized on the way out so a record written before a feed kind
        # existed still reports a complete list.
        feeds=normalize_feeds(config.feeds),
        # Deprecated mirrors of the two Wodify providers — see UserConfigView.
        wodify_owner=WodifyOwnerConfigView(
            api_key=_status(owner.credential) if owner else _status(None),
            location=owner.location if owner else None,
            program=owner.program if owner else None,
            enabled=bool(owner and owner.enabled),
        ),
        wodify_member=WodifyMemberConfigView(
            whiteboard_key=_status(member.credential) if member else _status(None),
            location=member.location if member else None,
            program=member.program if member else None,
            enabled=bool(member and member.enabled),
        ),
    )


def to_me(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
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

    # Deprecated aliases first, so a request carrying both has `gyms` win.
    if update.wodify_member is not None:
        _apply_connection(
            config,
            GymProvider.WODIFY_MEMBER,
            GymConnectionUpdate(
                credential=update.wodify_member.whiteboard_key,
                location=update.wodify_member.location,
                program=update.wodify_member.program,
                enabled=update.wodify_member.enabled,
            ),
        )
    if update.wodify_owner is not None:
        _apply_connection(
            config,
            GymProvider.WODIFY_OWNER,
            GymConnectionUpdate(
                credential=update.wodify_owner.api_key,
                location=update.wodify_owner.location,
                program=update.wodify_owner.program,
                enabled=update.wodify_owner.enabled,
            ),
        )
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
