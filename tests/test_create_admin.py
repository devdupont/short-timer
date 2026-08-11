"""`scripts/create_admin.py` — the only way to make an account without an invite.

Scripts are the blind spot in this suite: the app's setup runs in a FastAPI
lifespan and the tests' runs in `conftest.py`, so a standalone script that
forgets a startup step is green everywhere except in the hands of whoever runs
it. This one was broken that way once — see `tests/cache/test_db.py` for the
guard on the underlying cause.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from shortimer.model.register import LoginRequest
from shortimer.model.status import Role
from shortimer.users import get_user_by_email

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_admin.py"


def _load_script() -> ModuleType:
    """Import the script by path: `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("create_admin", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


create_admin = _load_script()


def _run_with(
    monkeypatch: pytest.MonkeyPatch, *argv: str, password: str = "a-long-password"
) -> None:
    """Answer the script's prompts: confirm the database, then type a password twice."""
    monkeypatch.setattr(sys, "argv", ["create_admin.py", *argv])
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda _prompt="": password)


async def test_creates_a_verified_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: a first account with the role that can mint invites.

    It's created already verified because nobody emailed the address a link —
    but whoever ran the command had shell access to the server, which is the
    stronger claim of the two.
    """
    _run_with(monkeypatch, "boss@example.com", "--name", "Boss")

    assert await create_admin.main() == 0

    user = await get_user_by_email("boss@example.com")
    assert user is not None
    assert user.role is Role.ADMIN
    assert user.email_verified
    assert user.display_name == "Boss"


async def test_refuses_an_address_that_could_never_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reserved TLD passes `str` and fails `EmailStr`, which login uses.

    Without this check the script cheerfully writes a user who exists in the
    database and is rejected by `/api/auth/login` forever after.
    """
    _run_with(monkeypatch, "boss@example.test")

    assert await create_admin.main() == 1
    assert await get_user_by_email("boss@example.test") is None


async def test_creates_nothing_when_the_database_is_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt naming the database is the guard against running against production."""
    _run_with(monkeypatch, "boss@example.com")
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert await create_admin.main() == 1
    assert await get_user_by_email("boss@example.com") is None


async def test_refuses_a_password_the_api_would_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same reasoning as the address: don't write an account login can't use."""
    _run_with(monkeypatch, "boss@example.com", password="short")

    assert await create_admin.main() == 1
    assert await get_user_by_email("boss@example.com") is None


async def test_refuses_a_second_account_for_the_same_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running it shouldn't quietly mint a duplicate."""
    _run_with(monkeypatch, "boss@example.com")
    assert await create_admin.main() == 0

    _run_with(monkeypatch, "boss@example.com")
    assert await create_admin.main() == 1


def _script_accepts(address: str) -> bool:
    validator: Any = create_admin._EMAIL
    try:
        validator.validate_python(address)
    except ValidationError:
        return False
    return True


def _login_accepts(address: str) -> bool:
    try:
        LoginRequest(email=address, password="a-long-password")
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize(
    "address",
    [
        "athlete@example.com",
        "athlete+tag@example.co.uk",
        # Reserved and special-use names: the easy way to create an account
        # that can never sign in, because only one of the two rejected them.
        "athlete@example.test",
        "athlete@localhost",
        "athlete@example.invalid",
        "not-an-address",
        "",
    ],
)
def test_accepts_exactly_the_addresses_login_accepts(address: str) -> None:
    """The two must agree, whichever way they change.

    Asserting the pair match rather than hard-coding a verdict per address
    means this keeps holding if the app ever loosens or tightens what counts
    as an address — the failure it exists to prevent is the two drifting apart,
    not any particular address being refused.
    """
    assert _script_accepts(address) == _login_accepts(address)
