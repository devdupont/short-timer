"""Outbound transactional email, via Postmark.

Three messages exist — an invite, an address verification, and a password
reset — and every one of them carries a link the recipient must be able to
trust. That's the whole job.

Postmark rather than a general ESP because it refuses to carry marketing mail
at all, which means the shared IP pools it puts us in contain only
transactional senders. Deliverability of a verification email is not a nice-to-
have here: an invite-only app where the invite lands in spam is an app nobody
can sign up for.

No SDK. Postmark's send API is one POST with a token header, and `httpx` is
already a dependency, so a client would be a dependency to carry, pin and audit
in exchange for nothing.

**Sending is off by default.** With `EMAIL_ENABLED=false` the link is written
to the log instead, which is what lets the whole signup flow be developed and
tested end to end without a provider, an account, or DNS.
"""

import logging
from dataclasses import dataclass

import httpx

from shortimer.config import get_settings

logger = logging.getLogger(__name__)

_POSTMARK_URL = "https://api.postmarkapp.com/email"

#: Postmark is a dependency of signup, not of serving workouts, so a slow
#: provider must not hold a request open indefinitely.
_TIMEOUT_SECONDS = 10.0


class EmailError(RuntimeError):
    """Raised when a message we needed to send could not be sent."""


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str


def _link(path: str, token: str) -> str:
    """A link into the *frontend*, carrying a token.

    Built against `public_base_url` because the recipient opens it in a
    browser, and the browser needs the site — not this API, which serves no
    HTML at all.
    """
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}{path}?token={token}"


def invite_message(to: str, token: str) -> Message:
    return Message(
        to=to,
        subject="You're invited to shortimer",
        text=(
            "You've been invited to shortimer, a programmable workout timer.\n\n"
            f"Set up your account:\n{_link('/register', token)}\n\n"
            f"This invite expires in {get_settings().invite_ttl_hours // 24} days.\n"
            "If you weren't expecting this, you can ignore it."
        ),
    )


def verify_message(to: str, token: str) -> Message:
    return Message(
        to=to,
        subject="Confirm your email for shortimer",
        text=(
            "Confirm this address to finish setting up your shortimer account:\n"
            f"{_link('/verify', token)}\n\n"
            f"This link expires in {get_settings().verify_ttl_hours} hours.\n"
            "If you weren't expecting this, you can ignore it."
        ),
    )


def reset_message(to: str, token: str) -> Message:
    return Message(
        to=to,
        subject="Reset your shortimer password",
        text=(
            "Someone asked to reset the password for this shortimer account.\n\n"
            f"Reset it here:\n{_link('/reset', token)}\n\n"
            f"This link expires in {get_settings().reset_ttl_minutes} minutes, and "
            "using it signs out every device currently logged in.\n"
            "If this wasn't you, ignore this email — nothing has changed yet."
        ),
    )


async def send(message: Message) -> None:
    """Deliver a message, or log it when sending is switched off.

    Raises `EmailError` when a send was attempted and failed, so a caller can
    tell "we couldn't reach them" apart from "we chose not to try".
    """
    settings = get_settings()

    if not settings.email_enabled or not settings.postmark_server_token:
        # The link is the whole payload in development, so log it at a level
        # that shows up without turning on debug logging.
        logger.info(
            "Email is disabled; not sending %r to %s. Body:\n%s",
            message.subject,
            message.to,
            message.text,
        )
        return

    payload = {
        "From": settings.email_from,
        "To": message.to,
        "Subject": message.subject,
        "TextBody": message.text,
        "MessageStream": settings.postmark_message_stream,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _POSTMARK_URL,
                json=payload,
                headers={
                    "X-Postmark-Server-Token": settings.postmark_server_token,
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise EmailError(f"Could not reach the email provider: {exc}") from exc

    if response.status_code != httpx.codes.OK:
        # Postmark puts a machine-readable reason in the body; log it, but
        # don't hand it to the caller — it can name the recipient, and these
        # errors surface on endpoints that must not confirm who has an account.
        logger.error(
            "Postmark rejected a send to %s: %s %s",
            message.to,
            response.status_code,
            response.text[:500],
        )
        raise EmailError("The email provider rejected the message.")
