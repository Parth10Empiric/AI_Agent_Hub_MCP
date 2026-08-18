from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

"""
The semantic layer seam (Phase 2.3).

Phase2.md asks for a hybrid router: semantic understanding plus keyword
hints. This module is the semantic half — but it ships **disabled by
default**, and that is a deliberate engineering decision worth
explaining, because "we could add embeddings" is where a lot of
projects quietly acquire a 2 GB dependency they never needed.

Why the lexical layer comes first:

  - It is synchronous, offline and roughly 1 ms. Embeddings mean a
    network round-trip on every single user message, in the hot path,
    before the LLM has even been called.
  - It is deterministic. When a query mis-routes you can reproduce the
    exact comparison. Embedding similarity is a black box you can only
    tune by swapping models.
  - With 61 tools it is already accurate. Semantic search earns its
    cost when tool descriptions are long and varied; yours are four
    words each, so there is very little for an embedding to capture
    that the name and keyword lists do not already say.

Why the seam exists anyway:

  When you reach 300+ tools, or open the marketplace to third-party
  MCP servers whose vocabulary you cannot curate, the lexicon stops
  scaling and semantics start to pay for themselves. At that point you
  implement one class here and pass it to the router. Nothing else in
  the codebase changes.

This is dependency inversion: the router depends on the small
`EmbeddingProvider` interface, not on Ollama, OpenAI or torch.
"""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """
    Anything that can turn text into vectors.

    A Protocol rather than an abstract base class, so an implementation
    does not have to import or inherit from this module at all - it
    just needs the right method. That keeps provider packages
    independent of the Agent Engine.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Return one vector per input text, in the same order.
        """
        ...

    @property
    def is_enabled(self) -> bool:
        """
        False when the provider cannot serve requests, so the router
        can skip the semantic stage instead of failing.
        """
        ...


class NullEmbeddingProvider:
    """
    The default: no semantic layer.

    A "null object" rather than `None`. The router can call
    `provider.is_enabled` unconditionally instead of scattering
    `if provider is not None` checks through the scoring code, which is
    where subtle bugs hide.
    """

    __slots__ = ()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]

    @property
    def is_enabled(self) -> bool:
        return False


class OllamaEmbeddingProvider:
    """
    Semantic embeddings via the Ollama server you already run.

    Not wired in by default. To turn it on:

        ollama pull nomic-embed-text

        from agent.embeddings import OllamaEmbeddingProvider
        engine = AgentEngine(
            embedding_provider=OllamaEmbeddingProvider(),
        )

    Note the failure policy in `embed`: if Ollama is unreachable we
    return empty vectors rather than raising. A degraded router that
    falls back to lexical scoring is far better than a chat session
    that dies because an optional ranking signal was unavailable.
    Optional components must fail soft.
    """

    __slots__ = ("_model", "_cache", "_available")

    def __init__(self, model: str = "nomic-embed-text") -> None:
        self._model = model
        self._cache: dict[str, list[float]] = {}
        self._available = True

    @property
    def is_enabled(self) -> bool:
        return self._available

    def embed(self, texts: list[str]) -> list[list[float]]:

        if not self._available:
            return [[] for _ in texts]

        missing = [
            text
            for text in texts
            if text not in self._cache
        ]

        if missing:
            try:
                import ollama

                response = ollama.embed(
                    model=self._model,
                    input=missing,
                )

                vectors = response.get("embeddings", [])

                for text, vector in zip(missing, vectors):
                    self._cache[text] = list(vector)

            except Exception:
                # Disable permanently for this process. Retrying a
                # broken embedding server on every keystroke would add
                # latency to every message for no benefit.
                self._available = False
                return [[] for _ in texts]

        return [
            self._cache.get(text, [])
            for text in texts
        ]


def cosine_similarity(
    first: list[float],
    second: list[float],
) -> float:
    """
    Cosine similarity, clamped to 0.0 - 1.0.

    Cosine measures the ANGLE between two vectors, ignoring their
    length. That is what we want: a long tool description and a short
    query should be comparable on meaning, not on how much text there
    is.

    Raw cosine ranges from -1 to 1. We clamp negatives to 0 because
    "semantically opposite" is not a meaningful signal for routing, and
    a negative term would let one bad signal cancel out a good lexical
    match.
    """

    if not first or not second or len(first) != len(second):
        return 0.0

    dot = 0.0
    norm_first = 0.0
    norm_second = 0.0

    for value_first, value_second in zip(first, second):
        dot += value_first * value_second
        norm_first += value_first * value_first
        norm_second += value_second * value_second

    if norm_first <= 0.0 or norm_second <= 0.0:
        return 0.0

    similarity = dot / (
        math.sqrt(norm_first) * math.sqrt(norm_second)
    )

    return max(0.0, min(1.0, similarity))
