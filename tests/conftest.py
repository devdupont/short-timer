"""Shared fixtures: a mocked Mongo, an ASGI test client, and pre-built accounts/sessions."""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME", "shortimer_test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
# Sending is off, so the flows that email a token log it instead. Every test
# that needs a token reads it from the database rather than an inbox.
os.environ.setdefault("EMAIL_ENABLED", "false")

from collections.abc import AsyncIterator, Awaitable, Callable, Generator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockDatabase

from shortimer.app import app
from shortimer.auth.session import SESSION_COOKIE_NAME
from shortimer.cache import db as db_module
from shortimer.cache.session import create_session
from shortimer.config import get_settings
from shortimer.model.status import Role
from shortimer.model.user import User
from shortimer.service import llm as llm_module
from shortimer.users import create_user

#: The account `authed_client` signs in as. Long enough to clear the minimum.
TEST_EMAIL = "athlete@example.com"
TEST_PASSWORD = "correct-horse-battery-staple"

_real_list_collection_names = AsyncMongoMockDatabase.list_collection_names


async def _list_collection_names_compat(self: Any, *args: Any, **kwargs: Any) -> Any:
    """`init_beanie` calls this with `authorizedCollections`/`nameOnly` kwargs
    real MongoDB (and Motor) accept but plain `mongomock` doesn't implement."""
    kwargs.pop("authorizedCollections", None)
    kwargs.pop("nameOnly", None)
    return await _real_list_collection_names(self, *args, **kwargs)


@pytest.fixture(autouse=True)
async def _mock_mongo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets an isolated in-memory Mongo instead of a real one.

    The indexes are created too, not just the collections. One of them is load
    bearing rather than an optimisation: the unique index on `email` is what
    makes two concurrent registrations for the same address fail instead of
    both succeeding, so a suite without it would pass while production
    accumulated duplicate accounts.
    """
    client: AsyncMongoMockClient[Any] = AsyncMongoMockClient()
    monkeypatch.setattr(db_module, "get_client", lambda: client)
    monkeypatch.setattr(
        AsyncMongoMockDatabase, "list_collection_names", _list_collection_names_compat
    )
    await db_module.init_documents()
    await db_module.ensure_indexes()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    """Clear the cached `Settings` before and after each test, so env patches take effect.

    Every submodule test directory used to redeclare this identically; it's
    cheap and safe to run unconditionally, so it lives here once instead.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_anthropic_client() -> None:
    """Drop the cached Anthropic client between tests.

    The app caches it so the connection pool is reused (and not leaked) across
    requests, but each test gets its own event loop and may patch the client
    class, so a client held over from a previous test would be wrong.
    """
    llm_module._client.cache_clear()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An unauthenticated ASGI client against the real app, no network involved."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def account() -> User:
    """An ordinary, verified account — the default identity for tests."""
    return await create_user(
        email=TEST_EMAIL, password=TEST_PASSWORD, display_name="Me", email_verified=True
    )


@pytest.fixture
async def admin_account() -> User:
    """An admin, for the operator and administration surfaces."""
    return await create_user(
        email="admin@example.com",
        password=TEST_PASSWORD,
        display_name="Admin",
        role=Role.ADMIN,
        email_verified=True,
    )


@pytest.fixture
def sign_in_as() -> Callable[[AsyncClient, str], Awaitable[str]]:
    """Put a client in a real session for an arbitrary user id.

    Sessions live in the database now, so a test can't mint one by signing a
    payload — it has to create the row. This is what the cross-owner isolation
    tests use to become somebody else.
    """

    async def _sign_in(client: AsyncClient, user_id: str) -> str:
        """Create a real session for `user_id` and attach its cookie to `client`."""
        token = await create_session(user_id)
        client.cookies.set(SESSION_COOKIE_NAME, token)
        return token

    return _sign_in


@pytest.fixture
async def authed_client(
    client: AsyncClient, account: User, sign_in_as: Callable[[AsyncClient, str], Awaitable[str]]
) -> AsyncClient:
    """A client signed in as `account`."""
    await sign_in_as(client, account.id)
    return client


@pytest.fixture
async def admin_client(
    client: AsyncClient,
    admin_account: User,
    sign_in_as: Callable[[AsyncClient, str], Awaitable[str]],
) -> AsyncClient:
    """A client signed in as `admin_account`."""
    await sign_in_as(client, admin_account.id)
    return client
