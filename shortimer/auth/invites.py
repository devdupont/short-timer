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

import logging
from datetime import UTC, datetime, timedelta

from pymongo.results import UpdateResult

from shortimer.auth.tokens import hash_token, new_token
from shortimer.config import get_settings
from shortimer.model.register import Invite
from shortimer.model.status import Role
from shortimer.users import normalize_email
from shortimer.util.time import is_expired

logger = logging.getLogger(__name__)


async def create_invite(
    *, created_by: str, email: str | None = None, role: Role = Role.USER
) -> tuple[str, Invite]:
    """Mint an invite. Returns the raw token and the record describing it.

    The token is returned exactly once — it's stored hashed, so it cannot be
    shown again later, which is why the admin API has to surface it at the
    moment of creation. `token_hash` is written in a second step, straight to
    the collection, because it deliberately isn't a field on `Invite` — see
    the model's docstring.
    """
    now = datetime.now(UTC)
    token = new_token()
    invite = Invite(
        email=normalize_email(email) if email else None,
        role=role,
        created_by=created_by,
        created_at=now,
        expires_at=now + timedelta(hours=get_settings().invite_ttl_hours),
    )
    await invite.insert()
    await Invite.get_pymongo_collection().update_one(
        {"_id": invite.id}, {"$set": {"token_hash": hash_token(token)}}
    )
    return token, invite


def invite_link(token: str) -> str:
    """The URL whoever holds `token` opens to register.

    Built against `public_base_url`: the token is redeemed on the register
    *screen*, and this API serves no HTML. Lives here rather than at the one
    call site because the admin endpoint and `scripts/create_invite.py` both
    hand this link to a person, and two spellings of it would eventually
    disagree.
    """
    return f"{get_settings().public_base_url.rstrip('/')}/register?token={token}"


async def find_invite(token: str) -> Invite | None:
    """The invite a token names, valid or not. Callers decide what to do."""
    doc = await Invite.get_pymongo_collection().find_one({"token_hash": hash_token(token)})
    return Invite.model_validate({**doc, "id": doc["_id"]}) if doc is not None else None


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
    if is_expired(invite.expires_at):
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
    result = await Invite.find_one(Invite.id == invite_id, Invite.redeemed_at == None).update(  # noqa: E711
        {"$set": {"redeemed_at": datetime.now(UTC), "redeemed_by": user_id}}
    )
    return isinstance(result, UpdateResult) and result.modified_count == 1


async def list_invites(*, limit: int = 100) -> list[Invite]:
    """Every invite, newest first, for the admin screen."""
    return await Invite.find().sort("-created_at").limit(limit).to_list()


async def revoke_invite(invite_id: str) -> bool:
    """Delete an unredeemed invite. False if it doesn't exist or was used.

    A redeemed invite is left alone: it's now a record of how an account came
    to exist, and deleting it would lose that.
    """
    result = await Invite.find_one(Invite.id == invite_id, Invite.redeemed_at == None).delete()  # noqa: E711
    return result is not None and result.deleted_count == 1
