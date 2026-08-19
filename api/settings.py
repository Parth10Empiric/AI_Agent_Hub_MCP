from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# api/settings.py -> api/ -> the project root
#
# The .env path MUST be absolute. A relative ".env" is resolved against
# the CURRENT WORKING DIRECTORY, so the app would load its config only
# when started from the project root and silently fall back to defaults
# anywhere else.
#
# This is the same bug config.settings.resolve_path() already fixes for
# the Google token - a relative path that worked until something ran
# from a different directory.
BASE_DIR = Path(__file__).resolve().parent.parent


class APISettings(BaseSettings):
    """
    Configuration for the HTTP API.

    Deliberately separate from config/settings.py, which belongs to the
    MCP server. server.py runs as a SUBPROCESS and has no business
    knowing the key that signs login tokens.

    A field with no default is REQUIRED: if it is missing the app
    refuses to start. That is the point - a missing JWT secret should
    stop the server, not silently become None and fail on request 400.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",

        # .env also holds GITHUB_TOKEN, SLACK_BOT_TOKEN, OLLAMA_MODEL
        # and other MCP-server values. Without extra="ignore",
        # pydantic-settings REJECTS every key it does not declare and
        # the app will not boot.
        extra="ignore",

        case_sensitive=False,
    )

    # --- Database ---------------------------------------------------

    # Must use +asyncpg. Plain postgresql:// selects the SYNCHRONOUS
    # driver, which blocks the event loop on every query - the same
    # class of bug removed from agent/loop.py in step 4.
    database_url: str

    # --- Security ---------------------------------------------------

    # SecretStr prints as ********** in logs and tracebacks. The value
    # is read with .get_secret_value() only where it is actually used.
    jwt_secret_key: SecretStr
    credential_encryption_key: SecretStr

    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # --- Application ------------------------------------------------

    api_title: str = "Agent Hub API"
    api_version: str = "0.1.0"
    api_environment: str = "development"

    @property
    def is_production(self) -> bool:
        """
        Used to switch off the interactive docs in production.

        A property rather than a stored field so it can never drift out
        of sync with api_environment.
        """

        return self.api_environment.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> APISettings:
    """
    Return the one settings instance.

    Cached for two reasons:

    1. Reading and validating the .env file on every request would be
       wasted work.

    2. FastAPI's Depends(get_settings) must hand every endpoint the
       SAME object. Two instances would mean two sources of truth.

    Tests that need different settings call get_settings.cache_clear()
    first.
    """

    return APISettings()
