"""Runtime settings, loaded from the environment (see .env.example)."""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    session_cookie_secure: bool = Field(
        default=True, description="Set false only for plain-http local dev/tests."
    )
    #: Two clocks bound a session (see sessions.py). The idle deadline slides
    #: forward as the session is used; the absolute one never moves. Both are
    #: deliberately long — this is a timer opened at a gym, and signing someone
    #: out mid-workout costs more than the risk it avoids, now that sessions
    #: can actually be revoked.
    session_idle_seconds: int = 60 * 60 * 24 * 30
    session_absolute_seconds: int = 60 * 60 * 24 * 180

    # Keys encrypting per-user third-party credentials, newest first — the
    # first is used for new writes, the rest only to read values written
    # before the last rotation. Same NoDecode treatment as cors_origins.
    # Deliberately independent of anything session-related: rotating session
    # config must not render every stored credential unreadable. Empty is valid and
    # simply disables credential storage (see crypto.is_configured).
    secrets_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Accounts -----------------------------------------------------------
    #: Where the *frontend* lives. Every link we email — verify, reset,
    #: invite — is built against this, so it must be the site the user opens,
    #: not this API.
    public_base_url: str = "http://localhost:5173"
    #: Shortest password we'll accept. Length is the only user-chosen property
    #: that reliably predicts strength; composition rules mostly produce
    #: "Password1!" and a sticky note.
    password_min_length: int = 12
    #: How long each emailed token stays good for. Reset is deliberately the
    #: shortest — it's the one that takes over an account outright.
    invite_ttl_hours: int = 24 * 14
    verify_ttl_hours: int = 48
    reset_ttl_minutes: int = 60

    # --- Outbound email (Postmark) ------------------------------------------
    #: Off by default so local dev and CI never try to send. Tokens are logged
    #: instead, which is what makes the whole flow testable without a provider.
    email_enabled: bool = False
    postmark_server_token: str = ""
    #: Must be on the authenticated sending subdomain. shortimer.com publishes
    #: DMARC with strict alignment (adkim=s), so a From: on the apex fails
    #: authentication outright rather than merely landing in spam.
    email_from: str = "shortimer <no-reply@send.shortimer.com>"
    #: Postmark rejects a send whose stream doesn't exist; "outbound" is the
    #: default transactional stream every account starts with.
    postmark_message_stream: str = "outbound"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    #: Cap on a single parse. The SDK's default is 10 minutes, long enough for
    #: one hung call to tie up a request the caller has long since abandoned.
    anthropic_timeout_seconds: float = 45.0
    anthropic_max_retries: int = 1

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "short_timer"
    #: How long a request waits for a reachable server before giving up.
    #: PyMongo's default is 30 seconds, which turns a database blip into a
    #: pile-up: every request holds a worker for half a minute before the
    #: handler in `errors.py` can return its 503, and `/api/ready` — whose
    #: whole job is to answer quickly — is the slowest of them. Failing in
    #: seconds sheds load instead of absorbing it.
    mongodb_timeout_ms: int = 5_000

    #: How the MCP server authenticates. It has no session to derive an owner
    #: from — it's a local stdio tool, not an HTTP caller — so it presents a
    #: per-user API token instead (mint one under Settings → API tokens). This
    #: replaced an `MCP_OWNER_ID` that merely *named* an owner, which asserted
    #: an identity without proving it. Empty means the tools refuse.
    mcp_api_token: str = ""

    # NoDecode stops pydantic-settings JSON-decoding this before our validator
    # runs. Without it, the obvious CORS_ORIGINS=https://shortimer.com raises a
    # SettingsError and the container fails to start.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    @field_validator("cors_origins", "secrets_keys", "metrics_admin_user_ids", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        """Accept a comma-separated list, a JSON array, or a real list."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [item.strip() for item in text.split(",") if item.strip()]

    # --- Trusting the caller's address --------------------------------------
    # Rate limits are counted per client address, so a spoofable address means
    # a bypassable limit. Both settings default to trusting nothing: the
    # socket peer is used unless the deployment says otherwise.
    #
    #: A single-value header set by the platform's proxy and not forwardable by
    #: the caller — e.g. "fly-client-ip", "cf-connecting-ip". Preferred when
    #: available, because there's no list to mis-parse.
    client_ip_header: str | None = None
    #: Number of trusted proxies that *append* to X-Forwarded-For. Azure
    #: Container Apps' ingress is one such hop. Anything a client sends arrives
    #: to the left of what the proxies appended, so we count from the right;
    #: 0 disables X-Forwarded-For entirely.
    trusted_proxy_hops: int = 0

    # --- Abuse and cost controls -------------------------------------------
    rate_limit_enabled: bool = True
    #: Brute-force protection for the shared passcode, counted per client IP.
    login_attempts_per_15_min: int = 10
    #: Parsing costs an Anthropic call, so it's capped per caller and overall.
    #: The global cap is the backstop on total spend.
    llm_calls_per_hour_per_subject: int = 60
    llm_calls_per_hour_global: int = 500
    #: Bounds ordinary write traffic without getting in a real user's way.
    writes_per_minute_per_subject: int = 120
    #: Longest workout text we'll accept; guards token spend on huge pastes.
    max_workout_text_chars: int = 20_000

    # --- Metrics ------------------------------------------------------------
    #: Off switches the recording, not just the reading — a deployment that
    #: doesn't want an events collection shouldn't grow one.
    metrics_enabled: bool = True
    #: How long raw events are kept, enforced by a TTL index. Long enough to
    #: compare a month against the same month last year, which is the longest
    #: comparison anyone actually makes here.
    events_retention_days: int = 400
    #: Who may read the *operator* metrics — global spend and other users'
    #: activity. Empty means nobody, which is the right default: there are no
    #: roles yet, and a shared passcode would otherwise hand every visitor the
    #: Anthropic bill. Same NoDecode treatment as cors_origins.
    metrics_admin_user_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    return Settings()
