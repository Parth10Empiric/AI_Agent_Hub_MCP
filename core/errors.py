class MCPApplicationError(Exception):
    """Base exception for the MCP application."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "MCP_ERROR",
    ) -> None:
        super().__init__(message)

        self.message = message
        self.code = code


class ConfigurationError(MCPApplicationError):
    """Raised when application configuration is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="CONFIGURATION_ERROR",
        )


class AuthenticationError(MCPApplicationError):
    """Raised when authentication fails."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="AUTHENTICATION_ERROR",
        )


class ServiceError(MCPApplicationError):
    """Raised when an external service fails."""

    def __init__(
        self,
        message: str,
        *,
        service: str,
    ) -> None:
        super().__init__(
            message,
            code=f"{service.upper()}_SERVICE_ERROR",
        )

        self.service = service


class ToolExecutionError(MCPApplicationError):
    """Raised when an MCP tool fails."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="TOOL_EXECUTION_ERROR",
        )