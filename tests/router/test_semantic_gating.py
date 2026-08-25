from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.embeddings import OllamaEmbeddingProvider  # noqa: E402
from agent.router import (  # noqa: E402
    LEXICAL_DECISIVE,
    WEIGHT_SEMANTIC,
    ToolRouter,
)
from tests.tool_fixtures import build_router  # noqa: E402

"""
When the router pays for semantic search, and when it does not.

Embeddings were wired in because keyword scoring cannot rank a request
phrased as a GOAL - "summarise this repo" shares no word with any tool
in the corpus, so every candidate ties and the ordering is noise.

But they cost a network round trip. Measured on the development
machine:

    embed one query                    ~110ms
    everything else routing does        ~15ms

Paid on every message, that would make the embedding call the most
expensive thing in the router, and it would mostly be spent on requests
that did not need it: "list my github issues" is already ranked
correctly and decisively by keywords, and a signal weighted at 0.18
does not reorder a field that is already separated.

So it runs where it helps and nowhere else. This module pins down that
decision, because it is the kind of optimisation that silently stops
working - either by firing everywhere (slow) or nowhere (the original
bug, back again).
"""


class CountingProvider:
    """
    An embedding provider that records how often it was asked.

    Vectors are deterministic nonsense: this file is about WHETHER the
    provider is called, never about what it returns. A test that also
    depended on real similarity scores would need a model installed to
    run, which is a different kind of test.
    """

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [
            [float(len(text) % 7), 1.0, 0.5]
            for text in texts
        ]

    @property
    def is_enabled(self) -> bool:
        return True


class BrokenProvider:
    """One that claims to work and then raises."""

    @property
    def is_enabled(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding server is on fire")


def router_with(provider):
    router = build_router()
    router._embeddings = provider
    return router


# ---------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------


def test_a_query_that_names_tool_vocabulary_does_not_embed():
    provider = CountingProvider()
    router = router_with(provider)

    router.warm()

    calls_after_warm = provider.calls

    router.route("list my github issues")

    assert provider.calls == calls_after_warm


def test_a_query_phrased_as_a_goal_does_embed():
    provider = CountingProvider()
    router = router_with(provider)

    router.warm()

    calls_after_warm = provider.calls

    router.route("what is this project built with")

    assert provider.calls > calls_after_warm


def test_the_gate_is_the_strength_of_the_best_keyword_match():
    # Not a tie test. Six issue tools bunched at 0.87 is a confident
    # answer; eleven unrelated tools bunched at 0.19 is a coin toss.
    # Only the second one needs rescuing.
    assert ToolRouter._lexical_inconclusive([0.9, 0.87, 0.87, 0.87]) is False
    assert ToolRouter._lexical_inconclusive([0.27, 0.19, 0.19, 0.19]) is True
    assert ToolRouter._lexical_inconclusive([]) is True

    assert ToolRouter._lexical_inconclusive(
        [LEXICAL_DECISIVE + 0.01]
    ) is False


def test_semantics_weigh_more_when_keywords_failed():
    # A signal only promoted when the one it is replacing has gone
    # quiet. At its standing weight it could not break the 0.06 spread
    # that started all this.
    normal = ToolRouter._active_weights(True, lexical_inconclusive=False)
    rescued = ToolRouter._active_weights(True, lexical_inconclusive=True)

    assert rescued["semantic"] > normal["semantic"]
    assert normal["semantic"] > 0.0

    # Still normalised, whichever way it went.
    assert abs(sum(rescued.values()) - 1.0) < 1e-9
    assert abs(sum(normal.values()) - 1.0) < 1e-9


def test_semantics_never_outweigh_the_signals_that_can_be_explained():
    # It is the only signal that comes from a black box. It is allowed
    # to break a tie - the old 0.08 could not even do that - but a
    # ranking nobody can reproduce must not be able to overrule the
    # words the user actually typed or the service they named.
    weights = ToolRouter._active_weights(True)

    assert WEIGHT_SEMANTIC > 0.0
    assert weights["semantic"] < weights["lexical"]
    assert weights["semantic"] < weights["namespace"]


# ---------------------------------------------------------------------
# Failing soft
# ---------------------------------------------------------------------


def test_routing_survives_a_broken_embedding_server():
    # An OPTIONAL ranking signal must never be able to break a
    # conversation. Degraded routing beats no answer.
    router = router_with(BrokenProvider())

    decision = router.route("what is this project built with")

    assert decision.candidates


def test_warm_counts_vectors_not_keys():
    # A failed provider returns one EMPTY vector per tool, which the
    # cache stores faithfully - so counting keys reported "161 tools
    # embedded" for a provider that had just failed all 161, and the
    # startup log said the semantic layer was ready when it was not.
    router = router_with(BrokenProvider())

    assert router.warm() == 0


def test_warm_reports_what_it_actually_embedded():
    router = router_with(CountingProvider())

    assert router.warm() == len(router.index.tools)


def test_a_disabled_provider_is_never_called():
    router = build_router()

    assert router.warm() == 0

    decision = router.route("what is this project built with")

    assert all(
        candidate.breakdown.semantic == 0.0
        for candidate in decision.candidates
    )


# ---------------------------------------------------------------------
# The provider's own failure policy
# ---------------------------------------------------------------------


def test_the_first_call_failing_disables_the_provider():
    # A provider that fails on its very first call is misconfigured -
    # no model pulled, wrong host - and it was never going to work.
    # Retrying costs a full timeout on a real user's turn, every turn,
    # until a counter fills.
    provider = OllamaEmbeddingProvider(
        model="definitely-not-a-real-model",
        timeout=2.0,
    )

    provider.embed(["anything"])

    assert provider.is_enabled is False


def test_a_failure_after_success_is_forgiven():
    # ...but a provider that HAS worked is having a bad moment, and
    # a restart of the embedding server should not permanently
    # downgrade routing for the life of the process.
    provider = OllamaEmbeddingProvider(model="fake", timeout=0.1)

    provider._cache["known"] = [1.0, 0.0]
    provider._worked_once = True

    provider.embed(["something new"])

    assert provider.is_enabled is True
