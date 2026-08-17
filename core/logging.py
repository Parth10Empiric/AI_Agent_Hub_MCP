import logging
import sys

from config.settings import settings


def setup_logging() -> None:
    """
    Configure application logging.

    IMPORTANT:
    Logs are written to stderr because stdout is reserved
    for MCP JSON-RPC communication when using stdio transport.
    """

    level = getattr(
        logging,
        settings.log_level.upper(),
        logging.INFO,
    )

    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger for a module."""

    return logging.getLogger(name)