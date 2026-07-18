from fastapi import APIRouter, HTTPException, Response, status

from short_timer.auth import SESSION_COOKIE_NAME, check_passcode, create_session_token
from short_timer.config import get_settings
from short_timer.models import LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: LoginRequest, response: Response) -> None:
    if not check_passcode(body.passcode):
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
