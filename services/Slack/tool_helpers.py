from functools import wraps
from typing import Any, Callable

from core.logging import get_logger

from services.slack.errors import SlackError


logger = get_logger(__name__)


def slack_tool(func: Callable) -> Callable:
    """
    Common wrapper for Slack MCP tools.

    Handles Slack-specific exceptions consistently so individual
    MCP tools don't need repetitive try/except blocks.
    """

    @wraps(func)
    def wrapper(*args, **kwargs) -> dict[str, Any]:

        try:
            result = func(*args, **kwargs)

            return result

        except SlackError as exc:

            logger.error(
                "Slack tool '%s' failed: %s",
                func.__name__,
                exc,
            )

            return {
                "success": False,
                "error": str(exc),
            }

        except Exception as exc:

            logger.exception(
                "Unexpected error in Slack tool '%s'",
                func.__name__,
            )

            return {
                "success": False,
                "error": (
                    "Unexpected internal error while "
                    "executing Slack operation."
                ),
            }

    return wrapper