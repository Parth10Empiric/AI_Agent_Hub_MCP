import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.loop import run_agent


async def main():

    server_params = StdioServerParameters(
        command="python",
        args=["server.py"],
    )

    async with stdio_client(
        server_params
    ) as (read, write):

        async with ClientSession(
            read,
            write,
        ) as session:

            await session.initialize()

            tools_result = (
                await session.list_tools()
            )

            mcp_tools = tools_result.tools

            print(
                "\nProduction MCP Agent"
            )
            print("====================")

            print(
                f"Discovered "
                f"{len(mcp_tools)} MCP tools."
            )

            while True:

                user_message = input(
                    "\nYou: "
                ).strip()

                if not user_message:
                    continue

                if user_message.lower() in {
                    "exit",
                    "quit",
                }:
                    print(
                        "\nGoodbye!"
                    )
                    break

                answer = await run_agent(
                    session=session,
                    mcp_tools=mcp_tools,
                    user_message=user_message,
                )

                print(
                    f"\nAI: {answer}"
                )


if __name__ == "__main__":
    asyncio.run(main())