from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.discovery import KNOWN_NAMESPACES, ToolDiscovery  # noqa: E402
from agent.registry import ToolRegistry  # noqa: E402
from agent.router import ToolRouter  # noqa: E402
from agent.schemas import ToolDefinition  # noqa: E402

"""
Test fixtures built from the REAL MCP server source.

Rather than hand-maintaining a copy of the 161 tools, this module parses
`services/*/tools.py` with `ast` and extracts every tool's name and
docstring exactly as the MCP server would expose them.

Why parse instead of import?

    Importing `services.github.tools` would construct GitHubService(),
    which reads credentials and may open network clients. Tests must
    not need a GitHub token to check that a router ranks correctly.

    `ast` reads the file as text. No side effects, no credentials, no
    network - but the data is still the real data.

Why parse instead of a hardcoded list?

    A hardcoded fixture drifts. The day someone adds
    `github_delete_repository`, these tests would keep passing against
    a tool set that no longer exists. Parsing means the fixture is
    always in sync with the server, and a new tool shows up in the
    risk-classification tests automatically.
"""


SERVICES_DIR = PROJECT_ROOT / "services"


class FakeToolsResult:
    """
    Stands in for the object MCP's `list_tools()` returns.
    """

    def __init__(self, tools: list[dict]) -> None:
        self.tools = tools


def _docstring_of(node: ast.AST) -> str:
    """
    The tool's docstring, collapsed to a single line.

    MCP sends the docstring as the tool description, and the SDK
    collapses whitespace, so we do the same.
    """

    doc = ast.get_docstring(node) or ""

    return " ".join(doc.split())


_PYTHON_TO_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


def _json_type(annotation) -> dict:
    """
    Turn a Python type annotation into a JSON Schema fragment.

    Handles the forms your tools actually use:

        str                 -> {"type": "string"}
        int                 -> {"type": "integer"}
        str | None          -> {"type": ["string", "null"]}
        dict[str, Any]      -> {"type": "object"}

    Anything unrecognized returns {} - an empty schema matches
    everything, so an annotation we cannot read never causes a valid
    call to be rejected.
    """

    if annotation is None:
        return {}

    if isinstance(annotation, ast.Name):
        mapped = _PYTHON_TO_JSON_TYPE.get(annotation.id)
        return {"type": mapped} if mapped else {}

    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return {"type": "null"}

    # dict[str, Any] / list[dict] - only the outer type matters here.
    if isinstance(annotation, ast.Subscript):
        return _json_type(annotation.value)

    # str | None
    if isinstance(annotation, ast.BinOp) and isinstance(
        annotation.op, ast.BitOr
    ):
        types: list[str] = []

        for side in (annotation.left, annotation.right):
            fragment = _json_type(side)
            value = fragment.get("type")

            if isinstance(value, str) and value not in types:
                types.append(value)

        if not types:
            return {}

        return {"type": types[0] if len(types) == 1 else types}

    return {}


def _schema_from_signature(node) -> dict:
    """
    Build the tool's input schema from its function signature.

    This is roughly what FastMCP does at runtime when it turns a
    decorated function into an MCP tool: parameters become properties,
    and parameters WITHOUT a default become required.

    Deriving it here rather than hardcoding a stub matters for the
    executor tests. A stub schema validates nothing, so a test that
    claims "missing arguments are rejected" would pass while proving
    nothing at all.
    """

    args = node.args

    properties: dict[str, dict] = {}
    required: list[str] = []

    positional = list(args.posonlyargs) + list(args.args)
    first_with_default = len(positional) - len(args.defaults)

    for index, arg in enumerate(positional):

        if arg.arg in {"self", "cls"}:
            continue

        properties[arg.arg] = _json_type(arg.annotation)

        if index < first_with_default:
            required.append(arg.arg)

    for arg, default in zip(args.kwonlyargs, args.kw_defaults):

        properties[arg.arg] = _json_type(arg.annotation)

        if default is None:
            required.append(arg.arg)

    schema: dict = {
        "type": "object",
        "properties": properties,
    }

    if required:
        schema["required"] = required

    return schema


def extract_raw_tools() -> list[dict]:
    """
    Every MCP tool defined under `services/`, as plain dictionaries.
    """

    raw: list[dict] = []

    for tools_file in sorted(SERVICES_DIR.glob("*/tools.py")):

        tree = ast.parse(
            tools_file.read_text(encoding="utf-8"),
            filename=str(tools_file),
        )

        for node in ast.walk(tree):

            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue

            # Only the tool functions themselves, not the
            # `register_*_tools` wrappers around them.
            if not node.name.startswith(KNOWN_NAMESPACES):
                continue

            raw.append(
                {
                    "name": node.name,
                    "description": _docstring_of(node),
                    "inputSchema": _schema_from_signature(node),
                }
            )

    return raw


def build_tool_definitions() -> list[ToolDefinition]:
    """
    The real tools, normalized and classified by the Agent Engine.
    """

    discovery = ToolDiscovery(server_name="personal-mcp-server")

    return discovery.discover_from_result(
        FakeToolsResult(extract_raw_tools())
    )


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.replace_all(build_tool_definitions())
    return registry


def build_router() -> ToolRouter:
    return ToolRouter(build_registry())
