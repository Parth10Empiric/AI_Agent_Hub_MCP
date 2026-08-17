class GitHubError(Exception):
    """Base exception for GitHub-related errors."""
    
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)

        self.message = message
        self.status_code = status_code
        self.details = details or {}
        
class GitHubAuthenticationError(GitHubError):
    """Authentication failed."""


class GitHubPermissionError(GitHubError):
    """Authenticated but not authorized."""


class GitHubNotFoundError(GitHubError):
    """Requested resource was not found."""


class GitHubValidationError(GitHubError):
    """GitHub rejected the request because of invalid input."""


class GitHubRateLimitError(GitHubError):
    """GitHub rate limit was exceeded."""


class GitHubServerError(GitHubError):
    """GitHub server-side error."""


class GitHubAPIError(GitHubError):
    """Generic GitHub API error."""
    
