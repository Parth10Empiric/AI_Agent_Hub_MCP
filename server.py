from mcp.server import MCPServer

from services.github.tools import register_github_tools


mcp = MCPServer("personal-mcp-server")

register_github_tools(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")