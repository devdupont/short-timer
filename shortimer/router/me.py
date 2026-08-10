"""The current user, their configuration, and their account.

Credentials are write-only across this boundary: a client can set one, and can
see *that* one is set, but can never read it back. That's enforced by the
response models (`MeResponse` carries `SecretStatus`, not `SecretBox`) rather
than by remembering to strip fields here.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from shortimer.auth import api_tokens, passkeys
from shortimer.auth.passwords import verify_password
from shortimer.auth.session import require_session, session_token
from shortimer.cache.crypto import SecretsNotConfiguredError
from shortimer.cache.session import list_sessions, revoke_all_sessions
from shortimer.config import get_settings
from shortimer.model.passkey import Passkey, PasskeyChallengeResponse, PasskeyRegisterRequest
from shortimer.model.register import ChangePasswordRequest
from shortimer.model.token import ApiToken, ApiTokenCreatedResponse, ApiTokenCreateRequest
from shortimer.model.user import MeResponse, User, UserConfigUpdate
from shortimer.users import current_user, get_user, set_password, to_me, update_config
from shortimer.util.ratelimit import writes_allowed

router = APIRouter(prefix="/api/me", tags=["me"], dependencies=[Depends(require_session)])

CurrentUser = Annotated[User, Depends(current_user)]


async def _require_user(user_id: str) -> User:
    user = await get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("", response_model=MeResponse)
async def read_me(user: CurrentUser) -> MeResponse:
    return to_me(user)


@router.put("/config", response_model=MeResponse)
async def write_config(
    body: UserConfigUpdate, owner_id: Annotated[str, Depends(writes_allowed)]
) -> MeResponse:
    user = await _require_user(owner_id)
    try:
        updated = await update_config(user, body)
    except SecretsNotConfiguredError as exc:
        # A deployment problem, not a bad request — say so without implying the
        # user could fix it by resubmitting.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential storage is not configured on this server.",
        ) from exc
    return to_me(updated)


# --- Account -----------------------------------------------------------------


class SessionView(BaseModel):
    """One signed-in device, with nothing in it that could be replayed."""

    created_at: str | None = None
    last_seen_at: str | None = None
    user_agent: str | None = None


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    token: Annotated[str | None, Depends(session_token)],
) -> None:
    """Change the password, ending every *other* session.

    The current password is required even though the caller already holds a
    session. Sessions here are long-lived by design, so a borrowed laptop
    shouldn't be enough to lock the real owner out of their own account.

    This session is spared: signing someone out of the tab they just used to
    change their password reads as a failure, and it isn't the session we're
    worried about.
    """
    if len(body.new_password) < get_settings().password_min_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Password must be at least {get_settings().password_min_length} characters.",
        )
    if user.password_hash is None or not verify_password(user.password_hash, body.current_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Current password is incorrect."
        )

    await set_password(user.id, body.new_password)
    await revoke_all_sessions(user.id, except_token=token)


@router.get("/sessions", response_model=list[SessionView])
async def read_sessions(user: CurrentUser) -> list[SessionView]:
    """Where this account is currently signed in."""
    return [
        SessionView(
            created_at=_iso(row.get("created_at")),
            last_seen_at=_iso(row.get("last_seen_at")),
            user_agent=row.get("user_agent") if isinstance(row.get("user_agent"), str) else None,
        )
        for row in await list_sessions(user.id)
    ]


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def end_other_sessions(
    user: CurrentUser, token: Annotated[str | None, Depends(session_token)]
) -> None:
    """Sign out everywhere else, keeping this session."""
    await revoke_all_sessions(user.id, except_token=token)


def _iso(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


# --- Passkeys -----------------------------------------------------------------


@router.post("/passkeys/challenge", response_model=PasskeyChallengeResponse)
async def passkey_register_challenge(user: CurrentUser) -> PasskeyChallengeResponse:
    """Start registering a passkey against the signed-in account."""
    handle, options = await passkeys.start_registration(user)
    return PasskeyChallengeResponse(challenge_handle=handle, options=json.loads(options))


@router.post("/passkeys", response_model=Passkey)
async def register_passkey(body: PasskeyRegisterRequest, user: CurrentUser) -> Passkey:
    try:
        return await passkeys.finish_registration(
            user, body.challenge_handle, body.credential, body.nickname
        )
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/passkeys", response_model=list[Passkey])
async def read_passkeys(user: CurrentUser) -> list[Passkey]:
    return await passkeys.list_passkeys(user.id)


@router.delete("/passkeys/{credential_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_passkey(credential_id: str, user: CurrentUser) -> None:
    """Remove a passkey.

    Removing the last one is allowed: every account here also has a password
    and a verified address, so there's always a way back in. That would need
    revisiting the day passkey-only accounts exist.
    """
    if not await passkeys.delete_passkey(user.id, credential_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such passkey.")


# --- API tokens ---------------------------------------------------------------


@router.post("/tokens", response_model=ApiTokenCreatedResponse)
async def create_api_token(
    body: ApiTokenCreateRequest, user: CurrentUser
) -> ApiTokenCreatedResponse:
    """Mint a token, returning its value exactly once.

    Re-authenticated because this mints a credential that *outlives* the
    session used to create it — revoking every session wouldn't take it back.
    """
    if user.password_hash is None or not verify_password(user.password_hash, body.current_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Current password is incorrect."
        )
    if not body.scopes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A token needs at least one scope.",
        )

    raw, token = await api_tokens.create_token(user_id=user.id, name=body.name, scopes=body.scopes)
    return ApiTokenCreatedResponse(api_token=token, token=raw)


@router.get("/tokens", response_model=list[ApiToken])
async def read_api_tokens(user: CurrentUser) -> list[ApiToken]:
    return await api_tokens.list_tokens(user.id)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(token_id: str, user: CurrentUser) -> None:
    if not await api_tokens.revoke_token(user.id, token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such token.")
