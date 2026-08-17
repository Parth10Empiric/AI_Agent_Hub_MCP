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

            # -------------------------------------------------
            # Conversation history
            # -------------------------------------------------

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an AI assistant connected "
                        "to an MCP server.\n\n"

                        "Use available tools when they are "
                        "necessary.\n"

                        "Do not invent tool results.\n"

                        "Prefer the minimum number of tool "
                        "calls needed to answer the user.\n\n"

                        "IMPORTANT:\n"
                        "Maintain context from previous "
                        "messages in the current conversation.\n"

                        "When the user refers to something "
                        "such as 'it', 'them', 'those issues', "
                        "'that repository', or 'the previous "
                        "result', or need to a privios result in future"
                        "use the conversation history "
                        "to resolve the reference."
                    ),
                }
            ]

            # -------------------------------------------------
            # Main conversation loop
            # -------------------------------------------------

            while True:

                try:

                    user_message = input(
                        "\nYou: "
                    ).strip()

                except (EOFError, KeyboardInterrupt):

                    break

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
                    messages=messages,
                )

                print(
                    f"\nAI: {answer}"
                )


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("\nGoodbye!")