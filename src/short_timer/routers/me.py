"""The current user and their configuration.

Credentials are write-only across this boundary: a client can set one, and can
see *that* one is set, but can never read it back. That's enforced by the
response models (`MeResponse` carries `SecretStatus`, not `SecretBox`) rather
than by remembering to strip fields here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from short_timer.auth import current_owner, require_session
from short_timer.crypto import SecretsNotConfiguredError
from short_timer.models import MeResponse, User, UserConfigUpdate
from short_timer.ratelimit import writes_allowed
from short_timer.users import ensure_default_user, get_user, to_me, update_config

router = APIRouter(prefix="/api/me", tags=["me"], dependencies=[Depends(require_session)])


async def _require_user(user_id: str) -> User:
    user = await get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("", response_model=MeResponse)
async def read_me(owner_id: str = Depends(current_owner)) -> MeResponse:
    # A session can outlive the seed (a wiped database, a restart that skipped
    # startup maintenance), so recreate the default user rather than 404ing
    # someone who holds a valid cookie.
    if await get_user(owner_id) is None:
        await ensure_default_user()
    return to_me(await _require_user(owner_id))


@router.put("/config", response_model=MeResponse)
async def write_config(
    body: UserConfigUpdate, owner_id: str = Depends(writes_allowed)
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
