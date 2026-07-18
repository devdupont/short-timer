"""Shared-passcode auth.

There's no user model yet, just a single passcode (`APP_PASSCODE`). A
successful login gets a signed, expiring session cookie; every other route
requires that cookie to be present and valid. This is intentionally simple —
swap it for real accounts later without touching the rest of the app, since
routes only depend on `require_session`, not on any notion of a user.
"""

from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from short_timer.config import get_settings

SESSION_COOKIE_NAME = "short_timer_session"
_SESSION_SALT = "short-timer-session"
_INVALID_TOKEN_ERRORS = (BadSignature, SignatureExpired)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_SESSION_SALT)


def check_passcode(passcode: str) -> bool:
    return passcode == get_settings().app_passcode


def create_session_token() -> str:
    return _serializer().dumps({"authenticated": True})


def verify_session_token(token: str) -> bool:
    max_age = get_settings().session_max_age_seconds
    try:
        data = _serializer().loads(token, max_age=max_age)
    except _INVALID_TOKEN_ERRORS:
        return False
    return bool(data.get("authenticated"))


async def require_session(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> None:
    if session is None or not verify_session_token(session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
