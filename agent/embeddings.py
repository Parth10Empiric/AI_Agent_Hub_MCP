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
  - With 161 tools it is already accurate. Semantic search earns its
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

    __slots__ = (
        "_model",
        "_cache",
        "_available",
        "_failures",
        "_timeout",
        "_worked_once",
    )

    # A hung embedding server must not become a hung conversation.
    #
    # `embed` is SYNCHRONOUS and the router that calls it is called
    # from async request handlers, so a request with no timeout does
    # not slow one turn down - it blocks the event loop, and with it
    # every other user's request, MCP read and SSE heartbeat. The same
    # rule that made agent/loop.py insist on AsyncClient applies here,
    # and a timeout is the cheap half of it. The other half is warming
    # the cache at startup so the hot path embeds one short string.
    DEFAULT_TIMEOUT = 10.0

    # How many texts go in one request to the embedding server.
    #
    # Found by measurement, not by taste. Embedding all 161 tools in a
    # single call takes longer than DEFAULT_TIMEOUT on an ordinary
    # laptop, so the startup warm-up timed out, the provider concluded
    # it was misconfigured and disabled itself - and semantic routing
    # was silently off in exactly the deployment it was written for.
    #
    # Locally: 32 texts in ~2.4s, 64 in ~5.9s, one query in ~0.11s. A
    # batch that comfortably clears the timeout keeps the failure
    # detector meaningful, and 6 requests at startup cost nothing.
    #
    # This is why the timeout and the batch size have to be chosen
    # TOGETHER: a timeout is a statement about how long ONE request
    # should take, and it is meaningless if the caller decides how big
    # a request is somewhere else.
    BATCH_SIZE = 32

    # Consecutive failures tolerated ONCE THE PROVIDER HAS WORKED.
    #
    # The original version disabled itself on the first exception,
    # which is right for "you never pulled the model" and wrong for
    # "the server was restarting". The two are indistinguishable from a
    # single exception - but not from WHEN it happened:
    #
    #     fails on the very first call   misconfigured. It was never
    #                                    going to work, and every retry
    #                                    costs a full timeout on a real
    #                                    user's turn.
    #
    #     fails after succeeding         weather. Retry.
    #
    # So the first call is decisive and later ones are forgiven. That
    # matters because the first call is `warm()`, at startup, where a
    # permanent verdict costs nobody anything - and it means a
    # deployment with no embedding model pulled pays one timeout for
    # the life of the process instead of one per turn until the
    # counter fills.
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        model: str = "nomic-embed-text",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._model = model
        self._cache: dict[str, list[float]] = {}
        self._available = True
        self._failures = 0
        self._timeout = timeout
        self._worked_once = False

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

        for start in range(0, len(missing), self.BATCH_SIZE):

            batch = missing[start:start + self.BATCH_SIZE]

            try:
                import ollama

                response = ollama.Client(
                    timeout=self._timeout,
                ).embed(
                    model=self._model,
                    input=batch,
                )

                vectors = response.get("embeddings", [])

                # An empty reply is a failure that did not raise: the
                # server answered, with nothing. Treated as a failure
                # rather than cached, or every tool would be recorded
                # as having an empty vector and the provider would
                # report itself healthy forever.
                if not vectors:
                    raise RuntimeError("no embeddings returned")

                for text, vector in zip(batch, vectors):
                    self._cache[text] = list(vector)

                self._failures = 0
                self._worked_once = True

            except Exception:

                self._failures += 1

                # Give up for this process. Retrying a broken embedding
                # server on every message would add its full timeout to
                # every message, for no benefit.
                if (
                    not self._worked_once
                    or self._failures >= self.MAX_CONSECUTIVE_FAILURES
                ):
                    self._available = False
                    break

        # Whatever DID embed is returned, including on a partial
        # failure. A tool with no vector scores 0.0 on the semantic
        # signal, which is the same as running without embeddings -
        # so half an index is strictly better than none.
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
