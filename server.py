from core.logging import setup_logging, get_logger
from core.shutdown import GracefulShutdown
from core.tenancy import CredentialMiddleware, env_credentials_allowed

from mcp.server import MCPServer

from services.github.tools import register_github_tools
from services.google_drive.tools import register_google_drive_tools
from services.slack.tools import register_slack_tools
from services.google_calendar.tools import register_google_calendar_tools

def main():
    setup_logging()

    logger = get_logger(__name__)

    shutdown = GracefulShutdown()
    # shutdown.install_signal_handlers()

    logger.info("Starting Personal MCP Server")

    # PHASE 5.5: one middleware, every tool.
    #
    # It reads the caller's credentials out of the MCP request metadata
    # and puts them in ContextVars before the tool runs, so each of the
    # 161 tools talks to the account of whoever asked - without any of
    # them knowing that per-user credentials exist.
    #
    # Registered here rather than in the four error decorators because
    # the Drive tools have no decorator, and because a new tool must
    # not be able to forget it.
    mcp = MCPServer(
        "My MCP Server",
        middleware=[CredentialMiddleware()],
    )

    if env_credentials_allowed():
        logger.warning(
            "Environment credentials are ENABLED: an ANONYMOUS request "
            "with no credentials will use the tokens in .env. Correct "
            "for local development; set MCP_ENVIRONMENT=production to "
            "refuse. A request made on a USER's behalf never falls back "
            "here in any environment - see core.tenancy._resolve."
        )

    register_github_tools(mcp)
    register_google_drive_tools(mcp)
    register_slack_tools(mcp)
    register_google_calendar_tools(mcp)

    logger.info(
        "MCP services registered successfully"
    )
    
    mcp.run()

if __name__ == "__main__":
    main()