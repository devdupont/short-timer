"""Runtime settings, loaded from the environment (see .env.example)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_passcode: str = Field(..., description="Shared passcode required to use the app.")
    session_secret: str = Field(..., description="Secret used to sign session cookies.")
    session_max_age_seconds: int = 60 * 60 * 24 * 30
    session_cookie_secure: bool = Field(
        default=True, description="Set false only for plain-http local dev/tests."
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "short_timer"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
