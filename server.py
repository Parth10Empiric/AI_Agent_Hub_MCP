from core.logging import setup_logging, get_logger
from core.shutdown import GracefulShutdown

from mcp.server import MCPServer

from services.github.tools import register_github_tools
from services.google_drive.tools import register_google_drive_tools

def main():
    setup_logging()

    logger = get_logger(__name__)

    shutdown = GracefulShutdown()
    # shutdown.install_signal_handlers()

    logger.info("Starting Personal MCP Server")

    mcp = MCPServer("My MCP Server")

    register_github_tools(mcp)
    register_google_drive_tools(mcp)

    logger.info(
        "MCP services registered successfully"
    )
    
    mcp.run()

if __name__ == "__main__":
    main()