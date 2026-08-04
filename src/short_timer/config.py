"""Runtime settings, loaded from the environment (see .env.example)."""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_passcode: str = Field(..., description="Shared passcode required to use the app.")
    session_secret: str = Field(..., description="Secret used to sign session cookies.")
    session_max_age_seconds: int = 60 * 60 * 24 * 30
    session_cookie_secure: bool = Field(
        default=True, description="Set false only for plain-http local dev/tests."
    )

    # Keys encrypting per-user third-party credentials, newest first — the
    # first is used for new writes, the rest only to read values written
    # before the last rotation. Same NoDecode treatment as cors_origins.
    # Deliberately not derived from session_secret: rotating the cookie secret
    # must not render every stored credential unreadable. Empty is valid and
    # simply disables credential storage (see crypto.is_configured).
    secrets_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    #: Cap on a single parse. The SDK's default is 10 minutes, long enough for
    #: one hung call to tie up a request the caller has long since abandoned.
    anthropic_timeout_seconds: float = 45.0
    anthropic_max_retries: int = 1

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "short_timer"

    #: The account the MCP server reads and writes as. One MCP process is
    #: launched by one person's client and has no session to carry a user id,
    #: so its tenancy is a property of the process — this is the counterpart of
    #: the web app's session cookie. Unset means the shared-passcode default
    #: user, which is what every pre-accounts row was backfilled to.
    mcp_owner_id: str | None = None

    # NoDecode stops pydantic-settings JSON-decoding this before our validator
    # runs. Without it, the obvious CORS_ORIGINS=https://shortimer.com raises a
    # SettingsError and the container fails to start.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    @field_validator("cors_origins", "secrets_keys", mode="before")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
