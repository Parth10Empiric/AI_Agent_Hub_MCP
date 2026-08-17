import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:

            await session.initialize()

            # tools = await session.list_tools()

            # print("\nAvailable MCP tools:\n")

            # for tool in tools.tools:
            #     print(
            #         f"- {tool.name}: "
            #         f"{tool.description}"
            #     )

            # result = await session.call_tool(
            #     "github_create_issue",
            #     {
            #         "owner": "Parth10Empiric",
            #         "repo": "Empira_HR",
            #         "title": "MCP server integration test",
            #         "body": (
            #             "This issue was created by my "
            #             "personal MCP server."
            #         ),
            #         "labels": [],
            #         "assignees": [],
            #     },
            # )

            # print("\nCreate issue result:")
            # print(result)
            
            result = await session.call_tool(
                "github_get_issue",
                {
                    "owner": "Parth10Empiric",
                    "repo": "Empira_HR",
                    "issue_number": 1,
                },
            )

            print("\nGitHub issue result:")
            print(result)


if __name__ == "__main__":
    asyncio.run(main())