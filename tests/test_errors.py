"""Infrastructure failures, turned into answers a caller can act on.

`tests/router/test_workouts.py` covers two of these through a real route. This
covers the handlers as a unit — including the ones no route test reaches, and
the CORS ordering the module docstring warns about, which nothing verified
until now.
"""

import anthropic
import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from pymongo.errors import PyMongoError

from shortimer.errors import register_error_handlers

ORIGIN = "http://localhost:5173"

_REQUEST = httpx.Request("POST", "https://api.anthropic.com")


def _app(*, handlers_first: bool = True) -> FastAPI:
    """An app with one route that raises whatever it's given.

    `handlers_first` exists so the ordering requirement can be tested from
    both sides — see the test at the bottom of this module.
    """
    app = FastAPI()

    if not handlers_first:
        app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_credentials=True)

    register_error_handlers(app)

    if handlers_first:
        app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_credentials=True)

    @app.get("/boom")
    async def boom() -> None:
        raise app.state.error

    return app


async def _get(app: FastAPI, error: Exception, *, origin: str | None = None) -> httpx.Response:
    """Raise `error` from the route and return the response the caller sees."""
    app.state.error = error
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    headers = {"Origin": origin} if origin else {}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/boom", headers=headers)


@pytest.mark.parametrize(
    ("error", "expected_status", "retry_after"),
    [
        (anthropic.APITimeoutError(request=_REQUEST), 504, "5"),
        (anthropic.APIConnectionError(request=_REQUEST), 503, "10"),
        (
            anthropic.RateLimitError(
                "slow down", response=httpx.Response(429, request=_REQUEST), body=None
            ),
            429,
            "30",
        ),
        (
            anthropic.AuthenticationError(
                "bad key", response=httpx.Response(401, request=_REQUEST), body=None
            ),
            503,
            None,
        ),
        (anthropic.APIError("boom", request=_REQUEST, body=None), 502, "5"),
        (PyMongoError("no primary available"), 503, "10"),
    ],
)
async def test_upstream_failures_become_retryable_answers(
    error: Exception, expected_status: int, retry_after: str | None
) -> None:
    """Each dependency failure gets its own status, and says whether to retry.

    A bare 500 tells a caller nothing about whether trying again is worth it,
    which is the whole reason these handlers exist.
    """
    response = await _get(_app(), error)

    assert response.status_code == expected_status
    assert response.headers.get("Retry-After") == retry_after


@pytest.mark.parametrize(
    "error",
    [
        anthropic.AuthenticationError(
            "invalid x-api-key sk-ant-secret",
            response=httpx.Response(401, request=_REQUEST),
            body=None,
        ),
        PyMongoError("mongodb+srv://admin:hunter2@cluster.example.net"),
        RuntimeError("assertion failed in /srv/app/internals.py"),
    ],
)
async def test_no_handler_repeats_what_it_was_told(error: Exception) -> None:
    """The caller gets a sentence written for them, never the exception text.

    Upstream messages routinely carry connection strings and key fragments;
    the whole point of logging server-side is that none of it goes out.
    """
    response = await _get(_app(), error)

    detail = response.json()["detail"]
    assert detail
    assert str(error) not in response.text
    assert "Traceback" not in response.text


async def test_an_unhandled_bug_is_a_generic_500() -> None:
    """Anything not named above still gets a sentence rather than a stack trace."""
    response = await _get(_app(), RuntimeError("a genuine bug"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Something went wrong. Please try again."}


async def test_a_handled_failure_still_carries_cors_headers() -> None:
    """The browser has to be able to *read* the error, not just receive it."""
    response = await _get(_app(), PyMongoError("no primary"), origin=ORIGIN)

    assert response.status_code == 503
    assert response.headers.get("access-control-allow-origin") == ORIGIN


async def test_an_unhandled_500_still_carries_cors_headers() -> None:
    """The invariant the whole module is arranged around.

    The catch-all is middleware rather than an `Exception` handler because
    Starlette routes `Exception` handlers to `ServerErrorMiddleware`, which
    sits *outside* CORS — so its response carries no
    `Access-Control-Allow-Origin`, a cross-origin browser blocks it, and the
    user is told it's a CORS problem instead of being shown the message.
    """
    response = await _get(_app(), RuntimeError("a genuine bug"), origin=ORIGIN)

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == ORIGIN


async def test_registering_the_handlers_after_cors_loses_those_headers() -> None:
    """Why `register_error_handlers` must be called before `add_middleware`.

    This asserts the *broken* arrangement stays broken, which is the point:
    it's the executable version of the warning in `app.py`, and the reason
    that comment can't just be deleted. If this ever fails because Starlette
    changed how middleware nests, the ordering requirement — and the comment
    describing it — is what needs revisiting.
    """
    response = await _get(_app(handlers_first=False), RuntimeError("a genuine bug"), origin=ORIGIN)

    assert response.status_code == 500
    assert "access-control-allow-origin" not in response.headers
