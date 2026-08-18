from __future__ import annotations

from typing import Any

from .classification import (
    build_permissions,
    classify_operation,
    classify_risk,
    split_tool_name,
)
from .lexicon import keywords_for_tool
from .schemas import ToolDefinition
from .text import normalize_token, split_identifier, tokenize

"""
Tool discovery and normalization (Phase 2.1 + 2.2).

This is the boundary between "the MCP protocol" and "our Agent Engine".
Everything on the far side of this module speaks ToolDefinition, and
nothing on the far side needs to know that MCP exists.

Discovery does four things to every tool it receives:

    1. Extract  - pull fields out of whatever shape the SDK returned
    2. Identify - work out which service it belongs to
    3. Classify - work out what it does and how dangerous it is
    4. Index    - precompute the terms it can be searched by

Steps 3 and 4 are done ONCE, here, at connection time. Doing them per
query would mean re-deriving the same immutable facts on every single
user message.
"""


# Longest prefix first. "google_calendar_list_events" starts with both
# "google_calendar_" and (if it were ever added) "google_", so the
# order of this tuple decides correctness. Sorting by length removes
# the trap instead of relying on whoever edits it next to notice.
KNOWN_NAMESPACES: tuple[str, ...] = tuple(
    sorted(
        (
            "google_drive",
            "google_calendar",
            "github",
            "slack",
        ),
        key=len,
        reverse=True,
    )
)


class ToolDiscovery:
    """
    Converts tools returned by an MCP ClientSession into the Agent
    Engine's internal ToolDefinition objects.
    """

    __slots__ = ("server_name",)

    def __init__(
        self,
        server_name: str = "personal-mcp-server",
    ) -> None:
        self.server_name = server_name

    # -----------------------------------------------------------------
    # Entry point
    # -----------------------------------------------------------------

    def discover_from_result(
        self,
        result: Any,
    ) -> list[ToolDefinition]:
        """
        Convert the result of MCP `list_tools()` into ToolDefinitions.

        The MCP SDK has changed some model details across versions, so
        this method intentionally handles both model objects and
        dictionary-like representations.
        """

        raw_tools = getattr(result, "tools", None)

        if raw_tools is None and isinstance(result, dict):
            raw_tools = result.get("tools")

        if raw_tools is None:
            return []

        return [
            self._normalize_tool(tool)
            for tool in raw_tools
        ]

    # -----------------------------------------------------------------
    # Normalization
    # -----------------------------------------------------------------

    def _normalize_tool(self, tool: Any) -> ToolDefinition:
        """
        Normalize one MCP tool into the internal representation.
        """

        name = self._get_value(tool, "name")

        if not name:
            raise ValueError("Discovered MCP tool has no name.")

        description = self._get_value(tool, "description")
        title = self._get_value(tool, "title")

        input_schema = (
            self._get_value(tool, "inputSchema")
            or self._get_value(tool, "input_schema")
            or {}
        )

        annotations = self._to_dict(
            self._get_value(tool, "annotations")
        )

        meta = self._to_dict(
            self._get_value(tool, "_meta")
            or self._get_value(tool, "meta")
        )

        namespace = self._detect_namespace(name, meta)

        # --- Classification (Phase 2.2) ------------------------------

        operation, source = classify_operation(
            tool_name=name,
            namespace=namespace,
            annotations=annotations,
        )

        risk_level = classify_risk(name, operation)

        _, resource = split_tool_name(name, namespace)

        permissions = build_permissions(
            namespace=namespace,
            resource=resource,
            operation=operation,
        )

        # --- Search index terms (Phase 2.3) --------------------------

        name_terms = self._index_terms(
            split_identifier(name)
        )

        description_terms = self._index_terms(
            tokenize(title) + tokenize(description)
        )

        keyword_terms = self._index_terms(
            [
                word
                for keyword in keywords_for_tool(name)
                for word in tokenize(keyword)
            ]
        )

        return ToolDefinition(
            name=name,
            title=title,
            description=description,
            input_schema=input_schema,
            server=self.server_name,
            namespace=namespace,
            resource=resource,
            operation=operation,
            risk_level=risk_level,
            permissions=permissions,
            classification_source=source,
            name_terms=name_terms,
            description_terms=description_terms,
            keyword_terms=keyword_terms,
            annotations=annotations,
            meta=meta,
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _index_terms(words: list[str]) -> tuple[str, ...]:
        """
        Reduce a word list to the canonical, de-duplicated index terms.

        Singularized so that the tool term and the user's word meet in
        the same form ("issues" and "issue" both become "issue"). Order
        is preserved and duplicates dropped, which keeps the index
        deterministic - important because a non-deterministic index
        makes routing bugs impossible to reproduce.
        """

        seen: dict[str, None] = {}

        for word in words:

            if len(word) < 2:
                continue

            seen.setdefault(normalize_token(word), None)

        return tuple(seen)

    @staticmethod
    def _get_value(obj: Any, key: str) -> Any:
        """
        Safely read a value from either a model object or a dictionary.
        """

        if isinstance(obj, dict):
            return obj.get(key)

        return getattr(obj, key, None)

    @staticmethod
    def _to_dict(value: Any) -> dict[str, Any]:
        """
        Convert common model types into dictionaries.
        """

        if value is None:
            return {}

        if isinstance(value, dict):
            return value

        if hasattr(value, "model_dump"):
            return value.model_dump()

        if hasattr(value, "dict"):
            return value.dict()

        return {}

    @staticmethod
    def _detect_namespace(
        tool_name: str,
        meta: dict[str, Any],
    ) -> str | None:
        """
        Determine the service namespace for a tool.

        Explicit metadata wins. The prefix convention is a fallback,
        because it is a convention of YOUR server and a third-party MCP
        server has no reason to follow it.
        """

        for key in ("namespace", "service", "provider"):

            value = meta.get(key)

            if isinstance(value, str) and value.strip():
                return value.strip()

        for namespace in KNOWN_NAMESPACES:

            if tool_name.startswith(f"{namespace}_"):
                return namespace

        return None
