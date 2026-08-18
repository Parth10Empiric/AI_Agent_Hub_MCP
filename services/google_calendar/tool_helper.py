import functools
import logging
from typing import Any, Callable

from .errors import (
    GoogleCalendarAuthError,
    GoogleCalendarNotFoundError,
    GoogleCalendarPermissionError,
    GoogleCalendarValidationError,
    GoogleCalendarAPIError,
)


logger = logging.getLogger(
    "mcp.google_calendar"
)


def handle_calendar_errors(
    func: Callable,
):
    """
    Centralized exception handler for Google Calendar MCP tools.

    Tool functions stay clean and only focus on:
        1. validating input
        2. calling the service
        3. returning the result
    """

    @functools.wraps(func)
    async def wrapper(
        *args: Any,
        **kwargs: Any,
    ):

        try:

            return await func(
                *args,
                **kwargs,
            )

        except GoogleCalendarValidationError as exc:

            logger.warning(
                "Calendar validation error in %s: %s",
                func.__name__,
                exc,
            )

            return {
                "success": False,
                "error": {
                    "type": "ValidationError",
                    "message": str(exc),
                },
            }

        except GoogleCalendarPermissionError as exc:

            logger.warning(
                "Calendar permission error in %s: %s",
                func.__name__,
                exc,
            )

            return {
                "success": False,
                "error": {
                    "type": "PermissionError",
                    "message": str(exc),
                },
            }

        except GoogleCalendarAuthError as exc:

            logger.error(
                "Calendar authentication error in %s: %s",
                func.__name__,
                exc,
            )

            return {
                "success": False,
                "error": {
                    "type": "AuthenticationError",
                    "message": str(exc),
                },
            }

        except GoogleCalendarNotFoundError as exc:

            logger.warning(
                "Calendar resource not found in %s: %s",
                func.__name__,
                exc,
            )

            return {
                "success": False,
                "error": {
                    "type": "NotFoundError",
                    "message": str(exc),
                },
            }

        except GoogleCalendarAPIError as exc:

            logger.error(
                "Calendar API error in %s: %s",
                func.__name__,
                exc,
            )

            return {
                "success": False,
                "error": {
                    "type": "APIError",
                    "message": str(exc),
                },
            }

        except Exception as exc:

            logger.exception(
                "Unexpected Calendar error in %s",
                func.__name__,
            )

            return {
                "success": False,
                "error": {
                    "type": "UnexpectedError",
                    "message": str(exc),
                },
            }

    return wrapper