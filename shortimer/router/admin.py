"""Operator-only account administration.

Everything here is gated on the `admin` role rather than `OPERATOR_ROLES`:
staff can read the privileged *metrics* because support work needs them, but
minting invites and changing roles is a different kind of power and shouldn't
come with a support hire.

Refusals are 404, not 403, matching the operator metrics — an endpoint you may
not use shouldn't confirm that it exists.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from shortimer.auth import invites
from shortimer.config import get_settings
from shortimer.model.register import Invite, InviteCreatedResponse, InviteCreateRequest
from shortimer.model.status import Role
from shortimer.model.user import User
from shortimer.users import require_role
from shortimer.util import email as email_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

Admin = Annotated[User, Depends(require_role(Role.ADMIN))]


class AdminUserView(BaseModel):
    """A user as the admin list shows them. No credential material at all."""

    id: str
    email: str | None
    email_verified: bool
    role: Role
    status: str
    display_name: str
    has_password: bool


@router.post("/invites", response_model=InviteCreatedResponse, response_model_by_alias=False)
async def create_invite(body: InviteCreateRequest, admin: Admin) -> InviteCreatedResponse:
    """Mint an invite, emailing it when an address was given.

    The raw token comes back either way. It's stored hashed and can never be
    shown again, so if the email fails — or there was no address to send to —
    this response is the only chance to get the link.
    """
    token, invite = await invites.create_invite(
        created_by=admin.id, email=body.email, role=body.role
    )
    link = invites.invite_link(token)

    emailed = False
    if invite.email is not None:
        try:
            await email_module.send(email_module.invite_message(invite.email, token))
            emailed = get_settings().email_enabled
        except email_module.EmailError:
            # Not an error for the caller: the invite exists and the link is
            # in this response, so the admin can pass it on by hand.
            logger.exception("Could not email the invite to %s.", invite.email)

    return InviteCreatedResponse(invite=invite, token=token, link=link, emailed=emailed)


@router.get("/invites", response_model=list[Invite], response_model_by_alias=False)
async def list_invites(admin: Admin) -> list[Invite]:
    """Every invite ever issued, redeemed or not."""
    return await invites.list_invites()


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: str, admin: Admin) -> None:
    """Revoke an unredeemed invite."""
    if not await invites.revoke_invite(invite_id):
        # Either it never existed or it's already been redeemed, and a
        # redeemed invite is kept on purpose as a record of how an account
        # came to exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No unredeemed invite with that id.",
        )


@router.get("/users", response_model=list[AdminUserView])
async def list_users(admin: Admin) -> list[AdminUserView]:
    """The 200 most recently created accounts."""
    from shortimer.cache.db import get_users_collection

    out: list[AdminUserView] = []
    async for doc in get_users_collection().find({}).sort("created_at", -1).limit(200):
        out.append(
            AdminUserView(
                id=doc["_id"],
                email=doc.get("email"),
                email_verified=bool(doc.get("email_verified", False)),
                role=Role(doc.get("role", Role.USER.value)),
                status=str(doc.get("status", "active")),
                display_name=str(doc.get("display_name", "")),
                # Whether one is set, never the hash itself.
                has_password=doc.get("password_hash") is not None,
            )
        )
    return out
