import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


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