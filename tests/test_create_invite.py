"""`scripts/create_invite.py` — minting an invite without an admin session.

Same blind spot as `test_create_admin.py`: a standalone script that forgets a
startup step is green everywhere except in the hands of whoever runs it. The
token is printed once and stored only as a hash, so these tests check the
printed link by redeeming it the way the register endpoint would.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlparse

import pytest

from shortimer.auth.invites import find_invite, invite_error, list_invites
from shortimer.model.status import Role

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_invite.py"


def _load_script() -> ModuleType:
    """Import the script by path: `scripts/` is not an importable package."""
    spec = importlib.util.spec_from_file_location("create_invite", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


create_invite_script = _load_script()


def _run_with(monkeypatch: pytest.MonkeyPatch, *argv: str, confirm: str = "y") -> None:
    """Set the command line and answer the database confirmation."""
    monkeypatch.setattr(sys, "argv", ["create_invite.py", *argv])
    monkeypatch.setattr("builtins.input", lambda _prompt="": confirm)


def _printed_token(capsys: pytest.CaptureFixture[str]) -> str:
    """The token out of the link the script printed — the only copy that exists."""
    link = next(line for line in capsys.readouterr().out.splitlines() if "/register?" in line)
    return parse_qs(urlparse(link).query)["token"][0]


async def test_mints_an_open_invite_that_anyone_can_redeem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No address means a code to hand over in person, valid for whoever holds it."""
    _run_with(monkeypatch)

    assert await create_invite_script.main() == 0

    invite = await find_invite(_printed_token(capsys))
    assert invite is not None
    assert invite.email is None
    assert invite.role is Role.USER
    assert invite_error(invite, "whoever@example.com") is None


async def test_mints_an_admin_invite_bound_to_one_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The escape hatch this script exists for: a new admin without an admin session."""
    _run_with(monkeypatch, "boss@example.com", "--role", "admin")

    assert await create_invite_script.main() == 0

    invite = await find_invite(_printed_token(capsys))
    assert invite is not None
    assert invite.role is Role.ADMIN
    assert invite_error(invite, "boss@example.com") is None
    # Bound means bound: the token alone doesn't let a different address in.
    assert invite_error(invite, "someone-else@example.com") is not None


async def test_refuses_an_address_the_register_endpoint_would_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reserved TLD passes `str` and fails `EmailStr`, so the invite would be a dead end."""
    _run_with(monkeypatch, "boss@example.test")

    assert await create_invite_script.main() == 1
    assert await list_invites() == []


async def test_mints_nothing_when_the_database_is_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt naming the database is the guard against running against production."""
    _run_with(monkeypatch, confirm="n")

    assert await create_invite_script.main() == 1
    assert await list_invites() == []


async def test_yes_skips_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """For a non-interactive run, where a blocked `input()` would hang."""
    monkeypatch.setattr(sys, "argv", ["create_invite.py", "--yes"])

    assert await create_invite_script.main() == 0
    assert len(await list_invites()) == 1


async def test_refuses_to_send_an_invite_with_no_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--send` with no address is a typo, and minting anyway would hide it."""
    _run_with(monkeypatch, "--send")

    assert await create_invite_script.main() == 1
    assert await list_invites() == []
