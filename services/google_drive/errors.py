class GoogleDriveError(Exception):
    """Base exception for Google Drive service errors."""


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