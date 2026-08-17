from mcp.server import MCPServer

from services.github.tools import register_github_tools
from services.google_drive.tools import register_google_drive_tools

mcp = MCPServer("personal-mcp-server")

register_github_tools(mcp)
register_google_drive_tools(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")