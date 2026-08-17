import re

from functools import wraps
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from .errors import GitHubError


def github_error_response(exc: GitHubError) -> dict[str, Any]:
    """Convert a GitHub exception into a consistent MCP response."""
    return {
        "success": False,
        "error": {
            "type": exc.__class__.__name__,
            "message": exc.message,
            "status_code": exc.status_code,
            "details": exc.details,
        },
    }


def validation_error_response(exc: ValueError) -> dict[str, Any]:
    """Convert validation errors into a consistent MCP response."""
    return {
        "success": False,
        "error": {
            "type": "ValidationError",
            "message": str(exc),
        },
    }


def github_tool(
    func: Callable[..., Awaitable[dict[str, Any]]],
):
    """
    Reusable error-handling wrapper for GitHub MCP tools.

    Catches:
        - GitHubError
        - ValueError

    Returns:
        Consistent MCP-friendly error responses.
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await func(*args, **kwargs)

        except GitHubError as exc:
            return github_error_response(exc)

        except ValueError as exc:
            return validation_error_response(exc)

    return wrapper


LINK_PATTERN = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')