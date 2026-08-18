from __future__ import annotations

from typing import Any, Iterable

from agent.schemas import ToolDefinition


class OllamaToolAdapter:
    """
    Converts internal ToolDefinition objects into the tool format
    expected by Ollama's chat API.
    """

    @staticmethod
    def convert(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            },
        }

    @classmethod
    def convert_many(
        cls,
        tools: Iterable[ToolDefinition],
    ) -> list[dict[str, Any]]:
        return [
            cls.convert(tool)
            for tool in tools
        ]