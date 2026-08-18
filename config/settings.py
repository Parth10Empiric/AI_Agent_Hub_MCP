import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def resolve_path(value: str | Path) -> Path:
    """
    Anchor a configured path to the project root.

    Paths in `.env` are usually written relative ("token.json"), which
    Python resolves against the CURRENT WORKING DIRECTORY. That is fine
    in a script and wrong in this project, because the MCP server runs
    as a subprocess whose working directory is wherever the client
    happened to be launched from.

    The symptom was subtle and expensive: Google Drive saved its OAuth
    token, could not find it again on the next run, and silently fell
    back to opening a browser consent window on every single call - a
    21-second delay per tool call that looked like a broken integration.

    Absolute paths are passed through untouched, so this is safe to
    apply to any configured path.
    """

    path = Path(value)

    return path if path.is_absolute() else (BASE_DIR / path)


@dataclass(frozen=True)
class Settings:
    """
    Application-wide configuration.

    Values are loaded from environment variables.
    """

    # ---------------------------------------------------------
    # Application
    # ---------------------------------------------------------

    app_name: str = os.getenv(
        "MCP_APP_NAME",
        "Personal MCP Server",
    )

    environment: str = os.getenv(
        "MCP_ENVIRONMENT",
        "development",
    )

    # ---------------------------------------------------------
    # Logging
    # ---------------------------------------------------------

    log_level: str = os.getenv(
        "MCP_LOG_LEVEL",
        "INFO",
    )

    # ---------------------------------------------------------
    # Google Drive
    # ---------------------------------------------------------

    google_credentials_path: str = os.getenv(
        "GOOGLE_CREDENTIALS_PATH",
        str(BASE_DIR / "credentials.json"),
    )

    google_token_path: str = os.getenv(
        "GOOGLE_TOKEN_PATH",
        str(BASE_DIR / "token.json"),
    )

    # ---------------------------------------------------------
    # Agent
    # ---------------------------------------------------------

    ollama_model: str = os.getenv(
        "OLLAMA_MODEL",
        "minimax-m3:cloud"
    )


settings = Settings()