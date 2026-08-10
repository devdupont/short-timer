"""Request/response shapes for login, registration, password reset, and invites."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from shortimer.model.base import MongoDocument
from shortimer.model.status import Role
from shortimer.util.time import utcnow


class LoginRequest(BaseModel):
    """Email/password sign-in."""

    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    """Redeeming an invite into an account.

    `password` has a floor but no ceiling worth enforcing beyond a sanity
    bound: Argon2 has no input length limit (this is the bcrypt trap the
    algorithm choice avoids), so a long passphrase is simply a better password.
    """

    invite_token: str
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    display_name: str = Field(default="", max_length=200)


class ForgotPasswordRequest(BaseModel):
    """Asks for a password-reset email. Always answers the same way — see `router/auth.py`."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Redeeming a password-reset token for a new password."""

    token: str
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    """A logged-in user replacing their own password."""

    #: Required even though the caller is already authenticated. A session is
    #: long-lived here, so possession of one shouldn't be enough to lock the
    #: real owner out of their own account.
    current_password: str
    new_password: str = Field(min_length=1, max_length=1024)


class VerifyEmailRequest(BaseModel):
    """Redeeming an email-verification token."""

    token: str


class InviteCreateRequest(BaseModel):
    """An admin minting a signup invite."""

    #: Omit for an open code that anyone holding it may redeem. Naming an
    #: address both restricts redemption and lets us skip the confirmation
    #: email, since delivery already proved control of the mailbox.
    email: EmailStr | None = None
    role: Role = Role.USER


class Invite(MongoDocument):
    """A signup invitation. The token itself is never part of this shape.

    `token_hash` is deliberately not a field here — see `auth/invites.py` —
    so returning this model directly as a response (see `router/admin.py`)
    can never leak it.
    """

    email: str | None = None
    role: Role = Role.USER
    created_by: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    redeemed_at: datetime | None = None
    redeemed_by: str | None = None

    class Settings:
        """Beanie collection name."""

        name = "invites"


class InviteCreatedResponse(BaseModel):
    """The one and only time the invite token is visible."""

    invite: Invite
    token: str
    #: Ready to paste to someone when no email was sent.
    link: str
    #: False when an address was named but the mail couldn't be delivered, so
    #: the admin knows to pass the link along by hand rather than assuming.
    emailed: bool


class InviteCheckResponse(BaseModel):
    """What the register screen needs before asking for a password."""

    valid: bool
    #: Pre-fills and locks the email field for an address-bound invite.
    email: str | None = None
    reason: str | None = None
