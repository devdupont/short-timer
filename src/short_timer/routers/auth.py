from fastapi import APIRouter, HTTPException, Request, Response, status

from short_timer.auth import SESSION_COOKIE_NAME, check_passcode, create_session_token
from short_timer.config import get_settings
from short_timer.models import LoginRequest
from short_timer.ratelimit import client_ip, enforce, login_limit, peek

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

    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)
