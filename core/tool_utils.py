from typing import Any

from core.errors import MCPApplicationError
from core.logging import get_logger


logger = get_logger(__name__)


def handle_tool_error(
    exc: Exception,
) -> dict[str, Any]:
    """
    Convert an exception into a safe MCP tool response.
    """

    if isinstance(exc, MCPApplicationError):

        logger.error(
            "%s: %s",
            exc.code,
            exc.message,
        )

        return {
            "success": False,
            "error": exc.message,
            "code": exc.code,
        }

    logger.exception(
        "Unexpected tool error"
    )

    return {
        "success": False,
        "error": "Internal tool error",
        "code": "INTERNAL_ERROR",
    }