"""Signup invitations.

Registration is invite-only, so this is the entire front door. Two kinds of
invite exist and the difference matters:

- **Address-bound** — issued to one email, and only that address may redeem it.
  Because the token was delivered *to* that mailbox, redeeming it already
  proves control of the mailbox, so the account is created verified and no
  confirmation email is sent. That halves the mail volume for the common case.
- **Open** — a code with no address attached, for handing to someone in person
  or over a channel that isn't email. Anyone holding it may register, so it
  proves nothing about the address they type, and those accounts must verify.

An invite is spent when it's redeemed and is kept afterwards rather than
deleted: an admin screen wants to show that an invite was used and by whom,
which is exactly the information deletion would throw away.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from shortimer.auth.tokens import hash_token, new_token
from shortimer.cache.db import get_invites_collection
from shortimer.config import get_settings
from shortimer.model.register import Invite
from shortimer.model.status import Role
from shortimer.users import normalize_email

logger = logging.getLogger(__name__)


def _from_document(doc: dict[str, Any]) -> Invite:
    data = dict(doc)
    data["id"] = data.pop("_id")
    data.pop("token_hash", None)
    return Invite(**data)


async def create_invite(
    *, created_by: str, email: str | None = None, role: Role = Role.USER
) -> tuple[str, Invite]:
    """Mint an invite. Returns the raw token and the record describing it.

    The token is returned exactly once — it's stored hashed, so it cannot be
    shown again later, which is why the admin API has to surface it at the
    moment of creation.
    """
    now = datetime.now(UTC)
    token = new_token()
    invite = Invite(
        id=uuid.uuid4().hex,
        email=normalize_email(email) if email else None,
        role=role,
        created_by=created_by,
        created_at=now,
        expires_at=now + timedelta(hours=get_settings().invite_ttl_hours),
    )

    document = invite.model_dump(mode="json")
    document["_id"] = document.pop("id")
    document["token_hash"] = hash_token(token)
    # Stored as real dates so the values stay comparable in a query, unlike
    # the ISO strings `model_dump(mode="json")` produces.
    document["created_at"] = invite.created_at
    document["expires_at"] = invite.expires_at
    await get_invites_collection().insert_one(document)
    return token, invite


async def find_invite(token: str) -> Invite | None:
    """The invite a token names, valid or not. Callers decide what to do."""
    doc = await get_invites_collection().find_one({"token_hash": hash_token(token)})
    return _from_document(doc) if doc is not None else None


def invite_error(invite: Invite | None, email: str) -> str | None:
    """Why this invite can't be redeemed by this address, or None if it can.

    Split out from redemption so the register endpoint can check an invite
    before doing any of the work of creating an account, and so the reasons
    are stated in one place rather than scattered through the route.
    """
    if invite is None:
        return "This invite link is not valid."
    if invite.redeemed_at is not None:
        return "This invite has already been used."
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if datetime.now(UTC) >= expires_at:
        return "This invite has expired."
    if invite.email is not None and invite.email != normalize_email(email):
        # Names the bound address deliberately: the holder of this token was
        # already told it by receiving the email, so there's nothing to leak,
        # and "wrong address" is otherwise a baffling failure.
        return f"This invite is for {invite.email}."
    return None


async def mark_redeemed(invite_id: str, user_id: str) -> bool:
    """Spend an invite. False if someone else got there first.

    The `redeemed_at: None` filter is what makes this safe against two
    registrations racing on the same token: the update matches at most once,
    and the loser is told the invite is used.
    """
    result = await get_invites_collection().update_one(
        {"_id": invite_id, "redeemed_at": None},
        {"$set": {"redeemed_at": datetime.now(UTC), "redeemed_by": user_id}},
    )
    return result.modified_count == 1


async def list_invites(*, limit: int = 100) -> list[Invite]:
    """Every invite, newest first, for the admin screen."""
    cursor = get_invites_collection().find({}).sort("created_at", -1).limit(limit)
    return [_from_document(doc) async for doc in cursor]


async def revoke_invite(invite_id: str) -> bool:
    """Delete an unredeemed invite. False if it doesn't exist or was used.

    A redeemed invite is left alone: it's now a record of how an account came
    to exist, and deleting it would lose that.
    """
    result = await get_invites_collection().delete_one({"_id": invite_id, "redeemed_at": None})
    return result.deleted_count == 1
