from core.errors import MCPApplicationError


class GoogleDriveError(MCPApplicationError):
    """
    Base exception for Google Drive service errors.

    Inherits from MCPApplicationError on purpose.

    `core.tool_utils.handle_tool_error()` - which every Drive tool uses
    - only reports real detail for MCPApplicationError. When this class
    inherited from plain Exception, every single Drive failure fell
    through to the generic branch and came back as:

        {"error": "Internal tool error", "code": "INTERNAL_ERROR"}

    An expired OAuth token, a missing file and a network outage all
    looked identical. The agent could not tell them apart, the retry
    logic could only classify them as UNKNOWN, and debugging meant
    guessing. One word of inheritance restores all of it.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "GOOGLE_DRIVE_ERROR",
    ) -> None:
        super().__init__(message, code=code)


class GoogleDriveAuthenticationError(GoogleDriveError):
    """Raised when Google Drive authentication fails."""


class GoogleDriveAPIError(GoogleDriveError):
    """Raised when a Google Drive API request fails."""


class GoogleDriveFileNotFoundError(GoogleDriveError):
    """Raised when a requested Google Drive file does not exist."""


class GoogleDriveUnsupportedFileTypeError(GoogleDriveError):
    """Raised when a file type cannot be read directly."""


class GoogleDriveFileOperationError(GoogleDriveError):
    """Raised when a file operation fails."""