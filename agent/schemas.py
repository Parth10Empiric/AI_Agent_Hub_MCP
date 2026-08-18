from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

"""
The Agent Engine's internal tool model (Phase 2.2 — normalization).

The whole point of this module is that the rest of the Agent Engine
never touches an MCP SDK object. MCP servers differ: naming styles,
description quality, whether they send annotations at all. If the
router, executor and permission engine each had to cope with that
variation, every one of them would carry the same defensive code.

Instead we normalize once, at discovery time, into `ToolDefinition`.
Everything downstream consumes this one shape.
"""


class Operation(str, Enum):
    """
    What a tool *does* to the outside world.

    This is the axis that permissions and approvals hang off, so it
    only has four values on purpose. More values would mean more
    branches in the permission engine for no real gain.

    ADMIN deserves its own value rather than being lumped under WRITE.
    Creating a file and granting a stranger access to your entire
    Drive are both "writes", but only one of them changes *who can
    see your data*. Phase 5's approval rules will treat them very
    differently.
    """

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"

    @property
    def is_mutating(self) -> bool:
        return self is not Operation.READ


class RiskLevel(str, Enum):
    """
    How much damage a tool can do if the agent calls it wrongly.

    Deliberately separate from `Operation`. The verb does not
    determine the danger:

        google_drive_create_file      WRITE  but recoverable   -> MEDIUM
        slack_send_message            WRITE  but irreversible  -> HIGH
        google_drive_create_permission ADMIN and exposes data  -> HIGH

    Risk is about **blast radius and reversibility**, not grammar.
    A deleted calendar event can be recreated. A Slack message sent to
    a customer cannot be unsent. Any system that derives risk purely
    from the verb will get this backwards.
    """

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def severity(self) -> int:
        """
        Numeric rank for comparisons.

        We do NOT override __lt__/__gt__ here. This class inherits
        from `str`, so those operators already exist and compare
        alphabetically — "critical" < "safe" would silently be True.
        An explicit `severity` is harder to misuse than a clever
        operator overload.
        """

        return _RISK_SEVERITY[self]


_RISK_SEVERITY: dict[RiskLevel, int] = {
    RiskLevel.SAFE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


# Any tool at or above this level needs a human to say yes, even if
# the user has already granted the underlying permission.
APPROVAL_RISK_THRESHOLD = RiskLevel.HIGH


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """
    One MCP tool, normalized for the Agent Engine.

    `frozen=True` means nobody can mutate a tool after discovery. That
    matters because the registry hands the same object to the router,
    the executor and (later) the permission engine. If any of them
    could edit it, a routing bug could silently change a risk level.

    `slots=True` removes the per-instance __dict__. With 61 tools it
    saves little memory, but it also makes typos fatal: assigning
    `tool.risk_lvl = ...` raises instead of silently creating a new
    attribute that nothing reads.
    """

    # --- Identity ----------------------------------------------------

    name: str
    description: str | None
    input_schema: dict[str, Any]

    # MCP server that exposed this tool.
    server: str

    title: str | None = None

    # Service this tool belongs to: github, slack, google_drive, ...
    namespace: str | None = None

    # The noun the tool acts on: issue, file, event, message, ...
    resource: str | None = None

    # --- Classification (Phase 2.2) ----------------------------------

    operation: Operation = Operation.WRITE
    risk_level: RiskLevel = RiskLevel.MEDIUM

    # Permission scopes this tool requires, e.g.
    #   ("github:issue:write", "github:*:write")
    # The wildcard form lets Phase 5 grant "all GitHub writes" without
    # enumerating every tool.
    permissions: tuple[str, ...] = ()

    # How the classification was reached: "annotation", "override",
    # "heuristic" or "default". Kept because when a tool is
    # mis-classified, the first question is always "which rule fired?"
    classification_source: str = "default"

    # --- Search index (Phase 2.3) ------------------------------------
    #
    # Precomputed at discovery time, never at query time. Tokenizing 61
    # tools on every keystroke would be pure waste: the tools do not
    # change between queries, only the query does.

    name_terms: tuple[str, ...] = ()
    description_terms: tuple[str, ...] = ()
    keyword_terms: tuple[str, ...] = ()

    # --- Original MCP metadata, preserved --------------------------

    annotations: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------
    # Derived properties
    # -----------------------------------------------------------------

    @property
    def read_only(self) -> bool:
        """
        True when the tool cannot change anything.

        Derived rather than stored so it can never disagree with
        `operation`. Two fields that must stay in sync are two fields
        that will eventually drift.
        """

        return self.operation is Operation.READ

    @property
    def category(self) -> str | None:
        """
        Grouping key for the UI. The service is the natural grouping.
        """

        return self.namespace

    @property
    def requires_approval(self) -> bool:
        """
        Whether a human must confirm before this tool runs.

        Two independent triggers, either one is enough:

          - the tool mutates state, or
          - the tool is high risk even if we classified it as a read
            (a bulk export is technically a read, but it moves data
            out of the account).

        This property is the seam Phase 5's approval flow plugs into.
        """

        return (
            self.operation.is_mutating
            or self.risk_level.severity
            >= APPROVAL_RISK_THRESHOLD.severity
        )

    @property
    def search_terms(self) -> tuple[str, ...]:
        """
        Every term this tool can be found by.
        """

        return (
            self.name_terms
            + self.description_terms
            + self.keyword_terms
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serializable form, for logs, the API layer and the future UI.
        """

        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "server": self.server,
            "namespace": self.namespace,
            "resource": self.resource,
            "operation": self.operation.value,
            "risk_level": self.risk_level.value,
            "read_only": self.read_only,
            "requires_approval": self.requires_approval,
            "permissions": list(self.permissions),
            "classification_source": self.classification_source,
            "annotations": self.annotations,
            "meta": self.meta,
        }
