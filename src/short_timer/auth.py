"""Session auth.

Authentication is still a single shared passcode (`APP_PASSCODE`), but a
successful login now resolves to a *user id* carried inside the signed session
cookie rather than a bare "authenticated" flag. `current_owner` reads that id,
which keeps it the single place tenancy is decided while making room for a
real login: adding signup means adding another way to mint a token, not
touching any of the queries that filter on the result.

Tokens issued before the id was carried are still honoured and resolve to the
default user, so shipping this doesn't sign existing sessions out.
"""

from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from short_timer.config import get_settings

SESSION_COOKIE_NAME = "short_timer_session"
_SESSION_SALT = "short-timer-session"
_INVALID_TOKEN_ERRORS = (BadSignature, SignatureExpired)

#: Owner every record belongs to while there's a single shared passcode.
DEFAULT_OWNER_ID = "default"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_SESSION_SALT)


def check_passcode(passcode: str) -> bool:
    return passcode == get_settings().app_passcode


def create_session_token(user_id: str = DEFAULT_OWNER_ID) -> str:
    return _serializer().dumps({"authenticated": True, "user_id": user_id})


def session_user_id(token: str) -> str | None:
    """The user a token authenticates, or None if it isn't valid.

    A token predating per-user sessions carries no `user_id`; it's still a
    signature we issued, so it resolves to the default user rather than being
    rejected and logging someone out mid-workout.
    """
    max_age = get_settings().session_max_age_seconds
    try:
        data = _serializer().loads(token, max_age=max_age)
    except _INVALID_TOKEN_ERRORS:
        return None
    if not isinstance(data, dict) or not data.get("authenticated"):
        return None
    user_id = data.get("user_id")
    return user_id if isinstance(user_id, str) and user_id else DEFAULT_OWNER_ID


def verify_session_token(token: str) -> bool:
    return session_user_id(token) is not None


async def require_session(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> None:
    if session is None or not verify_session_token(session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def current_owner(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> str:
    """The owner that stored data is scoped to.

    Every owner-scoped query filters on this value, so it stays the one place
    tenancy is decided — routers read the result and never the session itself.
    """
    user_id = session_user_id(session) if session is not None else None
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user_id
