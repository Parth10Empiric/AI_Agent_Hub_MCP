class GoogleCalendarError(Exception):
    """Base Google Calendar exception."""


class GoogleCalendarAuthError(GoogleCalendarError):
    """Authentication or authorization error."""


class GoogleCalendarNotFoundError(GoogleCalendarError):
    """Requested Calendar resource was not found."""


class GoogleCalendarPermissionError(GoogleCalendarError):
    """Google Calendar permission error."""


class GoogleCalendarValidationError(GoogleCalendarError):
    """Invalid Google Calendar request."""


class GoogleCalendarAPIError(GoogleCalendarError):
    """Unexpected Google Calendar API error."""