"""Registration, sign-in, and the two email-driven account recovery flows.

Two properties run through all of it.

**Nothing here confirms whether an address has an account.** Login answers the
same way for a wrong password and an unknown address; forgot-password answers
the same way whether or not it sent anything. An endpoint that distinguishes
them is an endpoint that enumerates your users, and on an invite-only app the
user list is exactly what an attacker would want.

**Rate limits are counted per IP *and* per address.** Per-IP alone lets someone
spray one password across many accounts from a botnet; per-address alone lets a
gym full of people behind one WiFi lock each other out. Only failures are
charged, so signing in correctly never costs anything.
"""

import json
import logging
import secrets
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from shortimer.auth import email_tokens, invites, passkeys
from shortimer.auth.email_tokens import TokenKind
from shortimer.auth.passwords import hash_password, verify_password
from shortimer.auth.session import current_owner, end_session, session_token, start_session
from shortimer.cache.session import revoke_all_sessions
from shortimer.config import get_settings
from shortimer.errors import not_found
from shortimer.metrics import record_login
from shortimer.model.passkey import PasskeyChallengeResponse, PasskeyLoginRequest
from shortimer.model.register import (
    ForgotPasswordRequest,
    InviteCheckResponse,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from shortimer.model.status import AccountStatus
from shortimer.model.user import MeResponse
from shortimer.users import (
    EmailAlreadyRegisteredError,
    create_user,
    get_user,
    get_user_by_email,
    mark_email_verified,
    normalize_email,
    rehash_if_needed,
    set_password,
    to_me,
)
from shortimer.util import email as email_module
from shortimer.util.ratelimit import client_ip, enforce, login_limit, peek

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: One message for every way a sign-in can fail. See the module docstring.
_BAD_CREDENTIALS = "Incorrect email or password."


@lru_cache
def _dummy_hash() -> str:
    """A real hash of a value nobody knows, for the unknown-address path.

    Argon2 is deliberately slow, so skipping the verify when no account exists
    would make an unknown address measurably faster to reject than a wrong
    password — the response time would answer the question the error message
    refuses to. Hashing a random value gives something genuine to burn the same
    work against. Computed once, on the first login rather than at import, so
    startup doesn't pay for it.
    """
    return hash_password(secrets.token_urlsafe(32))


def _too_short() -> HTTPException:
    """The 422 raised by every endpoint that accepts a new password."""
    minimum = get_settings().password_min_length
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Password must be at least {minimum} characters.",
    )


async def _charge_failure(request: Request, email: str) -> None:
    """Count one failed attempt against both the address and the caller."""
    await enforce(login_limit(), f"ip:{client_ip(request)}")
    await enforce(login_limit(), f"email:{normalize_email(email)}")


async def _check_limits(request: Request, email: str) -> None:
    """Reject if either the address or the caller is already over the login limit."""
    await peek(login_limit(), f"ip:{client_ip(request)}")
    await peek(login_limit(), f"email:{normalize_email(email)}")


# --- Registration ------------------------------------------------------------


@router.get("/invite", response_model=InviteCheckResponse)
async def check_invite(token: str) -> InviteCheckResponse:
    """Whether an invite link is usable, for the register screen to read.

    Deliberately takes no email: it's called before the visitor has typed one,
    so it can only report the invite's own validity. An address-bound invite
    returns its address, which the form then pre-fills and locks.
    """
    invite = await invites.find_invite(token)
    # Passing the invite's own address means a bound invite validates against
    # itself; an open one has nothing to check and passes.
    reason = invites.invite_error(invite, invite.email if invite and invite.email else "")
    if reason is not None:
        return InviteCheckResponse(valid=False, reason=reason)
    assert invite is not None  # invite_error returns a reason when it's None
    return InviteCheckResponse(valid=True, email=invite.email)


@router.post("/register", status_code=status.HTTP_204_NO_CONTENT)
async def register(body: RegisterRequest, request: Request, response: Response) -> None:
    """Redeem an invite into an account, and sign the new user in."""
    if len(body.password) < get_settings().password_min_length:
        raise _too_short()

    invite = await invites.find_invite(body.invite_token)
    reason = invites.invite_error(invite, body.email)
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    assert invite is not None

    # An invite sent *to* an address has already proved the recipient can read
    # that mailbox, so there is nothing left for a confirmation email to
    # establish. An open code proves nothing about the address typed into the
    # form, so those accounts still have to verify.
    verified = invite.email is not None

    try:
        user = await create_user(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            role=invite.role,
            email_verified=verified,
        )
    except EmailAlreadyRegisteredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email already has an account. Try signing in instead.",
        ) from None

    if not await invites.mark_redeemed(invite.id, user.id):
        # Lost a race with another registration on the same token. The account
        # exists but has no invite backing it, so refuse rather than let one
        # invite create two accounts.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This invite has already been used."
        )

    if not verified:
        await _send_verification(user.id, user.email or body.email)

    await record_login(owner_id=user.id)
    await start_session(response, request, user.id)


# --- Signing in and out ------------------------------------------------------


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: LoginRequest, request: Request, response: Response) -> None:
    """Email/password sign-in. Answers wrong-password and unknown-address identically."""
    await _check_limits(request, body.email)

    user = await get_user_by_email(body.email)
    # Verify against *something* even when the address is unknown, so the
    # response time doesn't quietly answer the question the error message
    # refuses to. Argon2 is slow by design, which would otherwise make an
    # unknown address visibly faster than a wrong password.
    stored = user.password_hash if user and user.password_hash else _dummy_hash()
    matched = verify_password(stored, body.password)

    if user is None or user.password_hash is None or not matched:
        await _charge_failure(request, body.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_BAD_CREDENTIALS)

    if user.status is not AccountStatus.ACTIVE:
        # Distinguishable from a bad password on purpose: the credential was
        # right, and telling someone their account is disabled is the only way
        # they can do anything about it.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account has been disabled."
        )

    await rehash_if_needed(user, body.password)
    await record_login(owner_id=user.id)
    await start_session(response, request, user.id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, token: Annotated[str | None, Depends(session_token)]) -> None:
    """End this session."""
    await end_session(response, token)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    response: Response,
    owner_id: Annotated[str, Depends(current_owner)],
    token: Annotated[str | None, Depends(session_token)],
) -> None:
    """End every session for this account, including this one."""
    await revoke_all_sessions(owner_id)
    await end_session(response, token)


# --- Signing in with a passkey ------------------------------------------------


@router.post("/passkey/challenge", response_model=PasskeyChallengeResponse)
async def passkey_login_challenge(request: Request) -> PasskeyChallengeResponse:
    """Start a passkey sign-in.

    Unauthenticated by necessity, and it reveals nothing: the options carry an
    empty `allow_credentials`, so the answer is the same whoever is asking.
    Still rate-limited per address, because it does write a challenge row.
    """
    await enforce(login_limit(), f"ip:{client_ip(request)}")
    handle, options = await passkeys.start_authentication()
    return PasskeyChallengeResponse(challenge_handle=handle, options=json.loads(options))


@router.post("/passkey/login", status_code=status.HTTP_204_NO_CONTENT)
async def passkey_login(body: PasskeyLoginRequest, request: Request, response: Response) -> None:
    """Finish a passkey sign-in and start the session."""
    try:
        user_id = await passkeys.finish_authentication(body.challenge_handle, body.credential)
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await get_user(user_id)
    if user is None:
        # The credential outlived the account it belonged to.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="That passkey is not registered."
        )
    if user.status is not AccountStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account has been disabled."
        )

    await record_login(owner_id=user.id)
    await start_session(response, request, user.id)


# --- Email verification ------------------------------------------------------


async def _send_verification(user_id: str, address: str) -> None:
    """Issue and send a verification link, tolerating a provider outage.

    A failed send must not fail the request that triggered it: the account is
    already created, and the user can ask for another email. Losing the account
    because Postmark had a bad minute would be the worse outcome.
    """
    token = await email_tokens.issue(TokenKind.VERIFY, user_id=user_id, email=address)
    try:
        await email_module.send(email_module.verify_message(address, token))
    except email_module.EmailError:
        logger.exception("Could not send a verification email to %s.", address)


@router.post("/verify", response_model=MeResponse)
async def verify_email(body: VerifyEmailRequest) -> MeResponse:
    """Redeem an email-verification token."""
    user_id = await email_tokens.redeem(TokenKind.VERIFY, body.token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That verification link is invalid or has expired.",
        )
    await mark_email_verified(user_id)
    user = await get_user(user_id)
    if user is None:
        not_found("User not found")
    return to_me(user)


@router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification(owner_id: Annotated[str, Depends(current_owner)]) -> None:
    """Re-send the verification email, or no-op if the caller is already verified."""
    user = await get_user(owner_id)
    if user is None or user.email is None or user.email_verified:
        # Nothing to do, and nothing worth reporting — a verified user asking
        # to verify again is a no-op, not an error.
        return
    await _send_verification(user.id, user.email)


# --- Password reset ----------------------------------------------------------


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(body: ForgotPasswordRequest, request: Request) -> None:
    """Start a reset. Always 204, whether or not the address has an account."""
    # Charged unconditionally, unlike login: there's no "success" here to
    # exempt, and without a limit this endpoint is a free way to send mail to
    # any address on someone else's reputation.
    await enforce(login_limit(), f"ip:{client_ip(request)}")

    user = await get_user_by_email(body.email)
    if user is None or user.status is not AccountStatus.ACTIVE:
        logger.info("Password reset requested for an address with no usable account.")
        return

    address = user.email or body.email
    token = await email_tokens.issue(TokenKind.RESET, user_id=user.id, email=address)
    try:
        await email_module.send(email_module.reset_message(address, token))
    except email_module.EmailError:
        logger.exception("Could not send a password reset email.")


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest) -> None:
    """Finish a reset, and sign out everywhere.

    Ending every session is the point of the whole exercise: the usual reason
    to reset is that someone else may have had the old password, and leaving
    their sessions alive would make the reset cosmetic.
    """
    if len(body.password) < get_settings().password_min_length:
        raise _too_short()

    user_id = await email_tokens.redeem(TokenKind.RESET, body.token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That reset link is invalid or has expired.",
        )

    await set_password(user_id, body.password)
    revoked = await revoke_all_sessions(user_id)
    # Completing a reset proves the address works, which is the same thing the
    # verification email was there to establish.
    await mark_email_verified(user_id)
    await email_tokens.revoke_all(user_id, TokenKind.VERIFY)
    logger.info("Password reset for %s; ended %d session(s).", user_id, revoked)
