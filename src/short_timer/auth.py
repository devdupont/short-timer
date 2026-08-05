"""Session auth.

This module answers exactly one question — *which user is this request?* — and
answers it by looking the session token up in the database (see `sessions.py`).
It deliberately knows nothing about user *records*; resolving an id to an
account, and anything role-shaped, lives in `users.py`, which is what keeps the
import direction one-way.

Authentication is still a single shared passcode. What changed is that a login
now mints a revocable server-side session rather than a signed cookie that
stays valid until it expires no matter what.
"""

from fastapi import Cookie, HTTPException, Request, Response, status

from short_timer.config import get_settings
from short_timer.sessions import create_session, resolve_session, revoke_session

#: Owner every record belongs to while there's a single shared passcode.
DEFAULT_OWNER_ID = "default"

_COOKIE_BASE_NAME = "short_timer_session"


def _cookie_name() -> str:
    """The session cookie's name, which depends on whether it can be `Secure`.

    The `__Host-` prefix is worth having: a browser refuses to accept such a
    cookie if it carries a `Domain` attribute, which stops anything else under
    shortimer.com from planting a session cookie that this API would then read
    as its own. That's a real shape of attack here, because the API and the
    site are deliberately sibling subdomains.

    The prefix also *requires* `Secure`, which plain-http local dev can't set,
    so the bare name is used there. The name is therefore configuration, not a
    constant, and both ends of the request have to agree on it.
    """
    return (
        f"__Host-{_COOKIE_BASE_NAME}" if get_settings().session_cookie_secure else _COOKIE_BASE_NAME
    )


#: Resolved once at import: FastAPI needs a fixed alias to bind the parameter
#: to, so flipping `session_cookie_secure` mid-process won't be noticed.
SESSION_COOKIE_NAME = _cookie_name()


def check_passcode(passcode: str) -> bool:
    return passcode == get_settings().app_passcode


async def start_session(response: Response, request: Request, user_id: str) -> str:
    """Sign a user in: create the session and set the cookie carrying it."""
    settings = get_settings()
    token = await create_session(user_id, user_agent=request.headers.get("user-agent"))
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        # The cookie should not outlive the session it names. The absolute
        # deadline is the one that never moves, so it's the honest max-age.
        max_age=settings.session_absolute_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return token


async def end_session(response: Response, token: str | None) -> None:
    """Sign a user out: delete the session, then clear the cookie.

    Order matters. Clearing the cookie alone would leave a working token in
    the database that anyone who captured it could keep using.
    """
    if token is not None:
        await revoke_session(token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


async def session_token(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> str | None:
    """The raw token, for the routes that need to revoke or preserve it."""
    return session


async def current_owner(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> str:
    """The owner that stored data is scoped to.

    Every owner-scoped query filters on this value, so it stays the one place
    tenancy is decided — routers read the result and never the session itself.
    """
    user_id = await resolve_session(session) if session is not None else None
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user_id


async def require_session(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> None:
    if session is None or await resolve_session(session) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
