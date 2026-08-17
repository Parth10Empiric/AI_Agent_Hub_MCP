import json
from typing import Any

from ollama import chat


MODEL = "minimax-m3:cloud"

MAX_TOOL_ROUNDS = 50

def mcp_tool_to_ollama_tool(tool: Any) -> dict:
    """
    Convert an MCP Tool definition into
    Ollama's function-tool schema.
    """
    
    if hasattr(tool, 'model_dump'):
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
    
async def run_agent(
    session: Any,
    mcp_tools: list[Any],
    user_message: str,
) -> str:
    
    # 1. Filter tools BEFORE calling the LLM.
    
    selected_tools = mcp_tools

    print(
        f"\nAvailable tools: {len(selected_tools)}"
    )
    
    ollama_tools = [mcp_tool_to_ollama_tool(tool) for tool in selected_tools]
    
    available_tool_names = { tool.name for tool in selected_tools}
    
    # 2. Conversation history.
    
    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI assistant connected to "
                "an MCP server. Use available tools when "
                "they are necessary. Do not invent tool "
                "results. Prefer the minimum number of "
                "tool calls needed to answer the user."
            ),
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    executed_calls: set[str] = set()
    
    # 3. Controlled agent loop.
    
    for round_number in range(1, MAX_TOOL_ROUNDS + 1):
        print(
            f"\nAgent round "
            f"{round_number}/{MAX_TOOL_ROUNDS}"
        )
        
        response = chat(
            model=MODEL,
            messages=messages,
            tools=ollama_tools,
            think=True,
        )
        
        messages.append(response.message)
        
        tool_calls = response.message.tool_calls or []

        if not tool_calls:
            return (
                response.message.content
                or "I could not determine the next action."
            )

        duplicate_detected = False
        
        # Execute tool calls.
        
        for call in tool_calls:
            tool_name = call.function.name
            arguments = call.function.arguments
            
            if tool_name not in available_tool_names:
                error_result = {
                    "success": False,
                    "error": {
                        "type": "ToolNotAvailable",
                        "message": (
                            f"Tool '{tool_name}' "
                            "was not selected for this request."
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
            
            # Detect repeated identical tool calls.
            
            call_key = (
                f"{tool_name}:"
                f"{json.dumps(arguments, sort_keys=True)}"
            )
            
            if call_key in executed_calls:
                duplicate_detected = True
                
                print(
                    f"[Duplicate tool call prevented] "
                    f"{tool_name}"
                )

                continue
            
            executed_calls.add(
                call_key
            )
            
            print(
                f"\n[Tool call] {tool_name}"
            )

            print(
                f"Arguments: {arguments}\n"
            )
            
            # MCP tool execution.
            
            result = await session.call_tool(
                tool_name,
                arguments,
            )

            result_text = serialize_mcp_result(
                result
            )

            print(
                f"[Tool result] {result_text}"
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": result_text,
                }
            )

        # If the model repeatedly asks for the same
        # tool/arguments, stop instead of looping forever.

        if duplicate_detected:
            return (
                "I stopped because the agent attempted "
                "to repeat an identical tool call."
            )

    # Maximum tool-round protection.

    return (
        "I stopped because the maximum number of "
        "tool execution rounds was reached."
    )
