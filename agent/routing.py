from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Operation, ToolDefinition

"""
Routing result types.

These are the router's public contract. Kept in their own module so
that callers (the agent loop today, the FastAPI layer in Phase 3, the
execution-timeline UI in Phase 2.8) can import the shapes without
importing the router implementation and its dependencies.

The recurring theme here is **explainability**. Every score carries the
reasons that produced it. That costs a little memory and buys three
things you will want badly:

  - Debugging. "Why did it call delete_file?" has an answer.
  - Tuning. You can see which signal dominated before changing weights.
  - UI. The Agent Hub execution timeline can show the user why the
    agent chose a tool, which is what makes an agent feel trustworthy
    instead of arbitrary.
"""


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """
    The individual signals behind one tool's final score.

    Stored rather than recomputed because by the time you want to know
    why a tool ranked where it did, the query context is gone.
    """

    lexical: float = 0.0
    namespace: float = 0.0
    operation: float = 0.0
    executability: float = 1.0
    semantic: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "lexical": round(self.lexical, 4),
            "namespace": round(self.namespace, 4),
            "operation": round(self.operation, 4),
            "executability": round(self.executability, 4),
            "semantic": round(self.semantic, 4),
        }


@dataclass(frozen=True, slots=True)
class ToolCandidate:
    """
    A tool the router considers relevant to the current request.
    """

    tool_name: str
    namespace: str | None
    score: float
    breakdown: ScoreBreakdown = field(
        default_factory=ScoreBreakdown
    )
    reasons: tuple[str, ...] = ()

    # The full definition, so callers do not have to go back to the
    # registry to find out whether this tool needs approval.
    tool: ToolDefinition | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "namespace": self.namespace,
            "score": round(self.score, 4),
            "breakdown": self.breakdown.to_dict(),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class NamespaceScore:
    """
    How strongly the query pointed at one service.
    """

    namespace: str
    score: float
    matched_aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "score": round(self.score, 4),
            "matched_aliases": list(self.matched_aliases),
        }


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """
    Everything the router concluded about one user message.
    """

    candidates: tuple[ToolCandidate, ...]

    # 0.0 - 1.0. How sure the router is that these are the right tools.
    confidence: float

    # Services the query pointed at, best first. Plural on purpose:
    # "find the GitHub issue and check Slack" is one request touching
    # two services, and collapsing that to a single winner is exactly
    # the bug that makes multi-service agents impossible.
    namespaces: tuple[str, ...] = ()

    namespace_scores: tuple[NamespaceScore, ...] = ()

    # True when the router was not confident and deliberately widened
    # the selection rather than risk starving the LLM.
    fallback_used: bool = False

    # What the user appeared to want to do. Drives the safety rules
    # that keep destructive tools out of read-shaped requests.
    intent: Operation | None = None

    # Tokens that matched nothing in the index - usually entity names
    # ("argus", "neeraj"). Surfaced because a token that should have
    # matched but did not is the single best signal that your lexicon
    # needs a new alias.
    unmatched_tokens: tuple[str, ...] = ()

    query: str = ""
    duration_ms: float = 0.0

    @property
    def tool_names(self) -> list[str]:
        return [
            candidate.tool_name
            for candidate in self.candidates
        ]

    @property
    def is_empty(self) -> bool:
        return not self.candidates

    def by_namespace(self) -> dict[str, list[str]]:
        """
        Selected tools grouped by service, for logging and the UI.
        """

        grouped: dict[str, list[str]] = {}

        for candidate in self.candidates:

            key = candidate.namespace or "unknown"

            grouped.setdefault(key, []).append(
                candidate.tool_name
            )

        return grouped

    def requires_approval(self) -> tuple[str, ...]:
        """
        Which selected tools would need human confirmation to run.

        The router does not block anything - enforcement belongs to the
        executor and Phase 5's permission engine. But surfacing it here
        lets the chat UI warn the user up front ("this may modify your
        calendar") instead of only at the moment of execution.
        """

        return tuple(
            candidate.tool_name
            for candidate in self.candidates
            if candidate.tool is not None
            and candidate.tool.requires_approval
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "confidence": round(self.confidence, 4),
            "namespaces": list(self.namespaces),
            "namespace_scores": [
                score.to_dict()
                for score in self.namespace_scores
            ],
            "intent": self.intent.value if self.intent else None,
            "fallback_used": self.fallback_used,
            "unmatched_tokens": list(self.unmatched_tokens),
            "duration_ms": round(self.duration_ms, 2),
            "candidates": [
                candidate.to_dict()
                for candidate in self.candidates
            ],
        }
