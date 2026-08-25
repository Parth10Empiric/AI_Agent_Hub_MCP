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

    # The key that encrypts plugin_connections.credentials_enc.
    #
    # Kept as the singular name it has always had, so no existing .env
    # breaks. For a rotation, use the plural below instead.
    credential_encryption_key: SecretStr

    # Comma-separated, NEWEST FIRST. Overrides the singular form.
    #
    #     CREDENTIAL_ENCRYPTION_KEYS=new_key,old_key
    #
    # Writes use the first; reads try each in turn. That is what makes
    # a key rotation a background job with no downtime - deploy this
    # with both keys, run scripts/rotate_credentials.py, then deploy
    # again with only the new one.
    #
    # Order is not cosmetic. Reversed, you would keep writing with the
    # key you are trying to retire.
    credential_encryption_keys: str = ""

    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # How long a tool call may wait for a human before it is denied.
    #
    # A ceiling, not a target. An approval that waits forever holds a
    # request slot and - until WebApproval commits - a database
    # connection, so "no answer" has to become "no" on its own. Five
    # minutes is long enough for someone to read the arguments and
    # short enough that a closed tab does not leak a turn.
    approval_timeout_seconds: int = 300

    # --- Semantic routing (Phase 5.8) -------------------------------

    # Whether the router may use embeddings alongside keyword scoring.
    #
    # The seam has existed since Phase 2.3 and was never switched on,
    # which meant the `semantic` column of every score breakdown in
    # production read 0.0000. That was fine while queries named tool
    # vocabulary ("list my github issues") and useless the moment one
    # did not ("summarise this repo"), because the keyword layer then
    # has nothing to work with and no second opinion to fall back on.
    #
    # A flag rather than a hard dependency: the provider fails soft to
    # lexical-only scoring, and this makes that a decision rather than
    # an accident.
    embeddings_enabled: bool = True

    # Small, fast, and already on the machine that serves the chat
    # model. `ollama pull nomic-embed-text` is the only setup step; if
    # it is missing, the provider disables itself and routing degrades
    # to exactly what it did before this flag existed.
    embedding_model: str = "nomic-embed-text"

    # --- Rate limits (Phase 5.7) ------------------------------------

    # Off switch, for local development and for the moment a limit
    # turns out to be wrong in production. A limiter you cannot disable
    # is a limiter nobody dares tune.
    rate_limit_enabled: bool = True

    # Unauthenticated. The only limit a stranger can reach, and the
    # reason it is keyed on the ACCOUNT as well as the IP: behind a
    # proxy every request shares one address, and one attacker would
    # otherwise lock out everybody.
    login_attempts: int = 5
    login_window_seconds: int = 900

    # COST limits. Every turn is model tokens plus API calls.
    api_requests_per_minute: int = 100
    agent_turns_per_hour: int = 30
    tool_calls_per_hour: int = 300

    # BLAST RADIUS limits, per AGENT rather than per user - the unit a
    # user configures, and the unit that gets compromised.
    write_ops_per_hour: int = 50

    # Tighter, and on a different axis: RISK, not operation.
    # google_drive_create_permission is an ADMIN operation and HIGH
    # risk - it is how a document reaches an address the user has never
    # heard of - and a limit keyed only on "critical" would miss it.
    dangerous_ops_per_hour: int = 5

    # --- OAuth (Phase 5.3) ------------------------------------------

    # Where the provider sends the browser back to. MUST match what is
    # registered with each provider, character for character.
    #
    # A CONSTANT, never taken from the request. A user-supplied
    # redirect_uri is how authorization codes get delivered to an
    # attacker: the provider will happily redirect anywhere the app
    # claims is its own callback.
    oauth_redirect_base: str = "http://localhost:3000"

    # Where the callback sends the browser once the connection is
    # stored. The user started in the app and must end up back in it.
    frontend_base_url: str = "http://localhost:3000"

    # How long an in-flight authorisation may take. Ten minutes is more
    # than enough to read a consent screen and short enough that a
    # `state` stolen from a browser history is useless.
    oauth_state_ttl_seconds: int = 600

    # Refresh this long BEFORE a token actually expires.
    #
    # Zero would mean "refresh when it has already failed", which is a
    # user-visible error every hour. A token that passes the check and
    # then expires in flight gives you the bad path anyway, so the skew
    # has to be larger than the longest request you expect.
    oauth_refresh_skew_seconds: int = 300

    # Per provider. Empty means "not configured", and the API then says
    # so plainly instead of redirecting to a broken consent screen.
    github_client_id: str = ""
    github_client_secret: SecretStr = SecretStr("")

    # Drive and Calendar share one Google OAuth client - same endpoints,
    # same consent screen, different scopes.
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")

    slack_client_id: str = ""
    slack_client_secret: SecretStr = SecretStr("")

    # --- Application ------------------------------------------------

    api_title: str = "Agent Hub API"
    api_version: str = "0.1.0"
    api_environment: str = "development"

    @property
    def encryption_keys(self) -> list[str]:
        """
        Every key the credential store should accept, newest first.

        The plural setting wins when it is set; otherwise the singular
        one is used as a one-element list. One place decides, so no
        caller has to know both forms exist.
        """

        if self.credential_encryption_keys.strip():
            return [
                key.strip()
                for key in self.credential_encryption_keys.split(",")
                if key.strip()
            ]

        return [self.credential_encryption_key.get_secret_value()]

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
