"""Turn infrastructure failures into responses a client can act on.

Without these, an Anthropic timeout or a dropped Mongo connection surfaces as
a bare 500 with a stack trace in the logs and nothing useful for the caller —
the UI just reports a generic failure and the user has no idea whether to
retry. Each handler logs the real cause server-side and returns a short,
non-leaky message.
"""

from __future__ import annotations

import logging

import anthropic
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)


def _json(status_code: int, detail: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(anthropic.APITimeoutError)
    async def _timeout(request: Request, exc: anthropic.APITimeoutError) -> JSONResponse:
        logger.warning("Anthropic request timed out: %s %s", request.method, request.url.path)
        return _json(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "The workout parser took too long to respond. Please try again.",
            retry_after=5,
        )

    @app.exception_handler(anthropic.APIConnectionError)
    async def _unreachable(request: Request, exc: anthropic.APIConnectionError) -> JSONResponse:
        logger.warning("Could not reach Anthropic: %s", exc)
        return _json(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The workout parser is unreachable right now. Please try again shortly.",
            retry_after=10,
        )

    @app.exception_handler(anthropic.RateLimitError)
    async def _upstream_throttled(request: Request, exc: anthropic.RateLimitError) -> JSONResponse:
        logger.warning("Anthropic rate limited us.")
        return _json(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "The workout parser is busy. Please try again in a moment.",
            retry_after=30,
        )

    @app.exception_handler(anthropic.AuthenticationError)
    async def _bad_key(request: Request, exc: anthropic.AuthenticationError) -> JSONResponse:
        # A deployment problem, not something the caller can fix — say so
        # plainly without hinting at credentials.
        logger.error("Anthropic rejected our API key; check ANTHROPIC_API_KEY.")
        return _json(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The workout parser is not configured correctly. Please contact the operator.",
        )

    @app.exception_handler(anthropic.APIError)
    async def _upstream_error(request: Request, exc: anthropic.APIError) -> JSONResponse:
        logger.exception("Anthropic call failed.")
        return _json(
            status.HTTP_502_BAD_GATEWAY,
            "The workout parser failed to respond. Please try again.",
            retry_after=5,
        )

    @app.exception_handler(PyMongoError)
    async def _database_down(request: Request, exc: PyMongoError) -> JSONResponse:
        logger.exception("Database error on %s %s", request.method, request.url.path)
        return _json(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The database is unavailable right now. Please try again shortly.",
            retry_after=10,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Last resort. Log everything, return nothing that describes internals.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _json(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Something went wrong. Please try again.",
        )
