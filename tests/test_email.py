"""The Postmark sender, and the messages built for it."""

from collections.abc import Generator

import httpx
import pytest
import respx
from httpx import Response

from shortimer.config import get_settings
from shortimer.util import email as email_module

POSTMARK = "https://api.postmarkapp.com/email"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sending_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "test-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://shortimer.com")
    get_settings.cache_clear()


# --- Links point at the site, not the API ------------------------------------


def test_links_are_built_against_the_public_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recipient opens these in a browser; the API serves no HTML."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://shortimer.com")
    get_settings.cache_clear()

    assert (
        "https://shortimer.com/register?token=abc"
        in email_module.invite_message("a@b.com", "abc").text
    )
    assert (
        "https://shortimer.com/verify?token=abc"
        in email_module.verify_message("a@b.com", "abc").text
    )
    assert (
        "https://shortimer.com/reset?token=abc" in email_module.reset_message("a@b.com", "abc").text
    )


def test_a_trailing_slash_does_not_double_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://shortimer.com/")
    get_settings.cache_clear()
    assert "https://shortimer.com/verify" in email_module.verify_message("a@b.com", "t").text


def test_the_reset_message_warns_that_it_signs_you_out() -> None:
    """It's a surprising side effect if you aren't told."""
    assert "signs out every device" in email_module.reset_message("a@b.com", "t").text


# --- Sending ------------------------------------------------------------------


@respx.mock
async def test_send_posts_the_expected_payload(sending_enabled: None) -> None:
    route = respx.post(POSTMARK).mock(return_value=Response(200, json={"MessageID": "1"}))

    await email_module.send(email_module.Message(to="a@b.com", subject="Subject", text="Body"))

    assert route.called
    request = route.calls.last.request
    assert request.headers["X-Postmark-Server-Token"] == "test-token"
    payload = __import__("json").loads(request.content)
    assert payload["To"] == "a@b.com"
    assert payload["Subject"] == "Subject"
    assert payload["TextBody"] == "Body"
    assert payload["MessageStream"] == "outbound"


@respx.mock
async def test_the_from_address_is_on_the_sending_subdomain(sending_enabled: None) -> None:
    """shortimer.com publishes adkim=s, so an apex From: fails outright."""
    route = respx.post(POSTMARK).mock(return_value=Response(200, json={}))
    await email_module.send(email_module.Message(to="a@b.com", subject="S", text="B"))

    payload = __import__("json").loads(route.calls.last.request.content)
    assert "@send.shortimer.com" in payload["From"]


@respx.mock
async def test_a_rejected_send_raises(sending_enabled: None) -> None:
    respx.post(POSTMARK).mock(return_value=Response(422, json={"ErrorCode": 300}))
    with pytest.raises(email_module.EmailError):
        await email_module.send(email_module.Message(to="a@b.com", subject="S", text="B"))


@respx.mock
async def test_a_provider_outage_raises_rather_than_hanging(sending_enabled: None) -> None:
    respx.post(POSTMARK).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(email_module.EmailError):
        await email_module.send(email_module.Message(to="a@b.com", subject="S", text="B"))


@respx.mock
async def test_an_error_does_not_leak_the_recipient(sending_enabled: None) -> None:
    """These surface on endpoints that must not confirm who has an account."""
    respx.post(POSTMARK).mock(return_value=Response(422, json={"ErrorCode": 300}))
    with pytest.raises(email_module.EmailError) as excinfo:
        await email_module.send(
            email_module.Message(to="secret@example.com", subject="S", text="B")
        )
    assert "secret@example.com" not in str(excinfo.value)


@respx.mock
async def test_sending_disabled_logs_instead_of_posting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What makes the whole signup flow testable without a provider."""
    route = respx.post(POSTMARK).mock(return_value=Response(200, json={}))

    with caplog.at_level("INFO"):
        await email_module.send(
            email_module.Message(to="a@b.com", subject="S", text="the-link-here")
        )

    assert not route.called
    assert "the-link-here" in caplog.text


@respx.mock
async def test_a_missing_token_does_not_attempt_a_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled but unconfigured must not post without credentials."""
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "")
    get_settings.cache_clear()
    route = respx.post(POSTMARK).mock(return_value=Response(200, json={}))

    await email_module.send(email_module.Message(to="a@b.com", subject="S", text="B"))
    assert not route.called
