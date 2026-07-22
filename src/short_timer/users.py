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
    MeResponse,
    User,
    UserConfig,
    UserConfigUpdate,
    UserConfigView,
    WodifyMemberConfig,
    WodifyMemberConfigUpdate,
    WodifyMemberConfigView,
    WodifyOwnerConfig,
    WodifyOwnerConfigUpdate,
    WodifyOwnerConfigView,
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


def to_view(config: UserConfig) -> UserConfigView:
    """Config with every credential reduced to set/not-set plus a mask."""
    return UserConfigView(
        wodify_owner=WodifyOwnerConfigView(
            api_key=_status(config.wodify_owner.api_key),
            location=config.wodify_owner.location,
            program=config.wodify_owner.program,
            enabled=config.wodify_owner.enabled,
        ),
        wodify_member=WodifyMemberConfigView(
            whiteboard_key=_status(config.wodify_member.whiteboard_key),
            location=config.wodify_member.location,
            program=config.wodify_member.program,
            enabled=config.wodify_member.enabled,
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


def _merged_owner(current: WodifyOwnerConfig, update: WodifyOwnerConfigUpdate) -> WodifyOwnerConfig:
    return WodifyOwnerConfig(
        api_key=_apply_secret(current.api_key, update.api_key),
        location=_apply_text(current.location, update.location),
        program=_apply_text(current.program, update.program),
        enabled=current.enabled if update.enabled is None else update.enabled,
    )


def _merged_member(
    current: WodifyMemberConfig, update: WodifyMemberConfigUpdate
) -> WodifyMemberConfig:
    return WodifyMemberConfig(
        whiteboard_key=_apply_secret(current.whiteboard_key, update.whiteboard_key),
        location=_apply_text(current.location, update.location),
        program=_apply_text(current.program, update.program),
        enabled=current.enabled if update.enabled is None else update.enabled,
    )


async def update_config(user: User, update: UserConfigUpdate) -> User:
    """Merge a partial config change into the user's stored config."""
    config = user.config.model_copy(deep=True)
    if update.wodify_owner is not None:
        config.wodify_owner = _merged_owner(config.wodify_owner, update.wodify_owner)
    if update.wodify_member is not None:
        config.wodify_member = _merged_member(config.wodify_member, update.wodify_member)

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
