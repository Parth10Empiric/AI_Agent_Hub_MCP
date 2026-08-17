from collections import Counter
import json
from typing import Any

from ollama import chat


MODEL = "minimax-m3:cloud"

MAX_TOOL_ROUNDS = 50

MAX_DUPLICATE_CALLS = 2


def mcp_tool_to_ollama_tool(
    tool: Any,
) -> dict:
    """
    Convert an MCP Tool definition into
    Ollama's function-tool schema.
    """

    if hasattr(tool, "model_dump"):

        data = tool.model_dump()

    else:

        data = {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
        }

    return {
        "type": "function",
        "function": {
            "name": data["name"],
            "description": data.get(
                "description",
                "",
            ),
            "parameters": data.get(
                "inputSchema",
                {
                    "type": "object",
                    "properties": {},
                },
            ),
        },
    }


def serialize_mcp_result(
    result: Any,
) -> str:
    """
    Convert an MCP result into JSON text.
    """

    if hasattr(result, "model_dump"):

        result = result.model_dump()

    return json.dumps(
        result,
        ensure_ascii=False,
        default=str,
    )


def normalize_arguments(
    arguments: Any,
) -> dict:
    """
    Normalize Ollama tool arguments.

    Depending on the Ollama version/model,
    arguments may already be a dict or may
    arrive as JSON text.
    """

    if arguments is None:
        return {}

    if isinstance(arguments, dict):
        return arguments

    if isinstance(arguments, str):

        try:

            parsed = json.loads(arguments)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

    return {}


def make_tool_call_key(
    tool_name: str,
    arguments: dict,
) -> str:
    """
    Create a stable identifier for a tool call.

    Example:

    github_get_repository:
    {"owner":"argus","repo":"test"}
    """

    return (
        f"{tool_name}:"
        f"{json.dumps(arguments, sort_keys=True)}"
    )


async def run_agent(
    session: Any,
    mcp_tools: list[Any],
    user_message: str,
    messages: list[dict],
) -> str:

    # 1. Add the new user message to existing conversation

    messages.append(
        {
            "role": "user",
            "content": user_message,
        }
    )

    # 2. Prepare MCP tools

    selected_tools = mcp_tools

    print(
        f"\nAvailable tools: "
        f"{len(selected_tools)}"
    )

    ollama_tools = [
        mcp_tool_to_ollama_tool(tool)
        for tool in selected_tools
    ]

    available_tool_names = {
        tool.name
        for tool in selected_tools
    }

    # 3. Duplicate-call tracking

    tool_call_counts = Counter()

    # 4. Controlled agent loop

    for round_number in range(
        1,
        MAX_TOOL_ROUNDS + 1,
    ):

        print(
            f"\nAgent round "
            f"{round_number}/"
            f"{MAX_TOOL_ROUNDS}"
        )

        # Ask Ollama

        response = chat(
            model=MODEL,
            messages=messages,
            tools=ollama_tools,
            think=True,
        )

        # Preserve the assistant response.

        messages.append(
            response.message
        )

        # Get tool calls

        tool_calls = (
            response.message.tool_calls
            or []
        )

        # No tool call = final answer

        if not tool_calls:

            return (
                response.message.content
                or (
                    "I could not determine "
                    "the next action."
                )
            )

        # Execute requested tools

        for call in tool_calls:

            tool_name = (
                call.function.name
            )

            arguments = normalize_arguments(
                call.function.arguments
            )

            # Validate tool exists

            if tool_name not in available_tool_names:

                error_result = {
                    "success": False,
                    "error": {
                        "type": (
                            "ToolNotAvailable"
                        ),
                        "message": (
                            f"Tool '{tool_name}' "
                            "was not selected "
                            "for this request."
                        ),
                    },
                }

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": json.dumps(
                            error_result
                        ),
                    }
                )

                continue

            # Detect repeated identical calls

            call_key = make_tool_call_key(
                tool_name,
                arguments,
            )

            tool_call_counts[call_key] += 1

            current_count = (
                tool_call_counts[call_key]
            )

            print(
                f"\n[Tool call] "
                f"{tool_name}"
            )

            print(
                f"Arguments: "
                f"{arguments}\n"
            )

            # Prevent infinite repetition

            if (current_count > MAX_DUPLICATE_CALLS):

                print(
                    "[Tool loop detected] "
                    f"{tool_name} was called "
                    "repeatedly."
                )

                error_result = {
                    "success": False,
                    "error": {
                        "type": (
                            "RepeatedToolCall"
                        ),
                        "message": (
                            "The same tool call was "
                            "requested repeatedly. "
                            "Choose another action "
                            "or provide a final answer."
                        ),
                    },
                }

                messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name,
                        "content": json.dumps(
                            error_result
                        ),
                    }
                )

                continue

            # Execute MCP tool

            try:

                result = await session.call_tool(
                    tool_name,
                    arguments,
                )

                result_text = (
                    serialize_mcp_result(
                        result
                    )
                )

                print(
                    f"[Tool result] "
                    f"{result_text}"
                )

            except Exception as exc:

                print(
                    f"[Tool execution error] "
                    f"{exc}"
                )

                result_text = json.dumps(
                    {
                        "success": False,
                        "error": {
                            "type": (
                                "ToolExecutionError"
                            ),
                            "message": str(exc),
                        },
                    },
                    ensure_ascii=False,
                )

            # Put tool result back into history.

            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": result_text,
                }
            )

    # Maximum rounds reached

    return (
        "I stopped because the maximum "
        "number of tool execution rounds "
        "was reached."
    )