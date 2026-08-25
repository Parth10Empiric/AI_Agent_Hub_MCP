class SlackError(Exception):
    """Base Slack service error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "SLACK_ERROR",
    ):
        super().__init__(message)

        self.message = message
        self.code = code


class SlackAuthenticationError(SlackError):
    """Slack authentication failed."""

    def __init__(self, message: str):
        super().__init__(
            message,
            code="SLACK_AUTHENTICATION_ERROR",
        )


class SlackAPIError(SlackError):
    """Slack API returned an error."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
    ):
        super().__init__(
            message,
            code="SLACK_API_ERROR",
        )

        self.error_code = error_code


class SlackNotFoundError(SlackError):
    """Requested Slack resource was not found."""

    def __init__(self, message: str):
        super().__init__(
            message,
            code="SLACK_NOT_FOUND",
        )


class SlackPermissionError(SlackError):
    """Slack permission is missing."""

    def __init__(self, message: str):
        super().__init__(
            message,
            code="SLACK_PERMISSION_ERROR",
        )