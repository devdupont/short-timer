from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from short_timer.auth import (
    DEFAULT_OWNER_ID,
    check_passcode,
    end_session,
    session_token,
    start_session,
)
from short_timer.metrics import record_login
from short_timer.models import LoginRequest
from short_timer.ratelimit import client_ip, enforce, login_limit, peek
from short_timer.users import ensure_default_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: LoginRequest, request: Request, response: Response) -> None:
    # A single shared passcode is guessable given unlimited attempts, so cap
    # them per client address. Only *failures* are charged: a gym full of
    # people behind one WiFi IP would otherwise lock itself out by logging in
    # legitimately, while a guesser still gets shut off after a few misses.
    subject = f"ip:{client_ip(request)}"
    await peek(login_limit(), subject)

    if not check_passcode(body.passcode):
        await enforce(login_limit(), subject)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect passcode")

    # The passcode is how you authenticate *as the default user*; the session
    # carries that id so tenancy has a real account behind it. A second login
    # method later mints a session for a different user, and nothing
    # downstream needs to change.
    await ensure_default_user()
    await record_login(owner_id=DEFAULT_OWNER_ID)
    await start_session(response, request, DEFAULT_OWNER_ID)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, token: str | None = Depends(session_token)) -> None:
    """End this session. Unlike the signed cookie it replaces, this is real."""
    await end_session(response, token)
