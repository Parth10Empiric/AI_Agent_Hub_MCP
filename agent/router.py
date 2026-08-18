from __future__ import annotations

import time
from dataclasses import replace

from .embeddings import (
    EmbeddingProvider,
    NullEmbeddingProvider,
    cosine_similarity,
)
from .index import ToolIndex
from .lexicon import (
    ALL_INTENT_VERBS,
    INTENT_VERBS,
    NAMESPACE_ALIASES,
    STOPWORDS,
)
from .registry import ToolRegistry
from .routing import (
    NamespaceScore,
    RoutingDecision,
    ScoreBreakdown,
    ToolCandidate,
)
from .schemas import Operation, ToolDefinition
from .text import best_match, fuzzy_ratio, normalize_token, tokenize

"""
Hybrid tool router (Phase 2.3).

The job: turn a sentence a human typed into the small set of tools the
LLM should be allowed to see for that turn.

Why not just send all 61 tools every time?

  - Cost. Tool schemas are tokens. 61 schemas is a large fixed tax on
    every single message, paid before the model has done any work.
  - Accuracy. Models get measurably worse at choosing as the candidate
    list grows. Fewer, better options beat more options.
  - Scale. This is the architecture that still works at 300 tools. If
    you skip it now, you rewrite the agent later instead of adding a
    plugin.

The pipeline, in order:

    query
      |
      v
    tokenize + drop stopwords
      |
      v
    detect intent          (read? write? delete?)
      |
      v
    score namespaces       (which services? typo-tolerant, MULTI)
      |
      v
    expand tokens          (resolve typos against the index vocabulary)
      |
      v
    score tools            (lexical + namespace + intent + semantic)
      |
      v
    fair selection         (guarantee each chosen service is represented)
      |
      v
    confidence / fallback  (never starve the LLM)
"""


# ---------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------
#
# Every magic number in this file lives here, named and explained.
# Scoring weights buried inline are the reason routers become
# untunable: you cannot adjust what you cannot find.

# Relative importance of each scoring signal.
WEIGHT_LEXICAL = 0.55
WEIGHT_NAMESPACE = 0.22
WEIGHT_OPERATION = 0.09
WEIGHT_EXECUTABILITY = 0.06
WEIGHT_SEMANTIC = 0.08

# How much a command verb counts as topical evidence.
#
# This exists because of a real failure. IDF is computed over only 61
# tools, so a perfectly ordinary English word that happens to appear in
# exactly ONE docstring gets treated as rare and therefore decisive.
#
# The word "check" appears in google_calendar_freebusy's description
# and nowhere else. So "check my calendar connection" scored freebusy
# with weight 3.45 - higher than the word "calendar" itself - and the
# agent was handed the one calendar tool that needs three arguments the
# user never supplied.
#
# Command verbs are GRAMMAR, not TOPIC. They are already consumed by
# intent detection. Scoring them again as rare content words is double
# counting. We do not drop them entirely, because a few ("search",
# "access", "permission") really are topical, so instead we scale their
# weight down to a tiebreaker.
INTENT_VERB_WEIGHT_SCALE = 0.25

# Penalty per required argument the query cannot plausibly supply.
#
# A tool the agent CANNOT run is worth less than an equally relevant
# tool it can. `google_calendar_list_calendars` needs nothing;
# `google_calendar_freebusy` needs time_min, time_max and calendar_ids.
# For "check my calendar connection" only one of those is usable.
#
# Kept deliberately small: it must break ties, never override topic.
# "get repository argus/test" should still reach github_get_repository
# even though that tool has two required arguments.
UNFILLED_ARGUMENT_PENALTY = 0.35

# Stop counting after this many unfilled arguments.
#
# The meaningful distinction is "needs nothing" versus "needs
# something" - the gap between 3 and 6 missing arguments barely
# matters, and letting the penalty grow without limit turned this
# signal into a decider instead of a tiebreaker. With the cap:
#
#     0 unfilled -> 1.00
#     1 unfilled -> 0.74
#     2 or more  -> 0.59
MAX_COUNTED_UNFILLED_ARGUMENTS = 2

# Minimum similarity for a query token to count as naming a service.
# Set just below the 0.80 typo floor in text.py, so any word that
# survives the edit budget registers here.
ALIAS_MATCH_THRESHOLD = 0.78

# Each ADDITIONAL distinct alias hit for the same service adds this
# much confidence, capped. "github issues commits" should beat a bare
# "github", because three independent signals agree.
ALIAS_AGREEMENT_BONUS = 0.05
ALIAS_AGREEMENT_CAP = 0.15

# A service joins the candidate pool if it scores within this margin
# of the best service. This is what makes multi-service work.
NAMESPACE_POOL_MARGIN = 0.25

# Never pool more than this many services for a single request.
#
# Raised from 3 to 4 so that "check github, drive, slack and calendar"
# - a completely reasonable request against a four-service server -
# can actually be served. Keep this equal to the number of services you
# expose, not higher: it is a sanity bound, not a target.
MAX_NAMESPACES = 4

# --- Tool budget -----------------------------------------------------
#
# The number of tools sent to the model SCALES with how many services
# the request touches. A fixed budget is wrong in both directions:
#
#     10 tools for a one-service question  -> wasteful, hurts accuracy
#     10 tools for a four-service question -> 2.5 tools per service,
#                                             not enough to do the job
#
# Measured on this server:
#
#     1 service  ->  8 tools
#     2 services -> 12 tools
#     3 services -> 16 tools
#     4 services -> 20 tools
BASE_TOP_K = 8
TOP_K_PER_EXTRA_SERVICE = 4

# Hard ceiling. Past roughly this many tools the model's accuracy falls
# off faster than the extra coverage helps.
MAX_TOP_K = 24

# Guaranteed slots for EVERY service the router selected - not only the
# top-scoring ones.
#
# Previously only "primary" services (within 0.10 of the best) got
# reserved slots. That broke on a real query: the user typed "calander"
# and the typo cost Google Calendar 0.20 of score, so it scored 0.80
# against three services at 1.00, missed the primary cutoff, and
# received exactly ONE tool. The agent could not check the calendar
# because it was never handed a usable calendar tool.
#
# If the router is confident enough to name a service, it is confident
# enough to give that service a working set of tools.
MIN_TOOLS_PER_NAMESPACE = 3

# How much to penalise a tool from a service the user did NOT ask for.
#
# From a real session:
#
#     "test drive connection"
#       -> 0.475  slack_auth_info      <-- a SLACK tool, ranked first
#          0.453  google_drive_search_files
#
# The user named Drive. Drive scored 1.00. Slack scored nothing at all.
# Yet slack_auth_info still won, because the words "test" and
# "connection" both appear in its text while "drive" only weakly
# matches Drive's own tools. Its lexical lead (0.60 vs 0.20) exactly
# cancelled the namespace advantage.
#
# Out-of-service tools must still be reachable - "who am I?" names no
# service and legitimately spans several. So the penalty scales with
# how confident we are about the service: certain about Drive means a
# Slack tool needs to be overwhelmingly better to get in; no service
# detected means no penalty at all.
CROSS_SERVICE_PENALTY = 0.5

# Drop candidates scoring below this fraction of the best candidate.
# Self-adjusting: when one tool clearly wins, the set shrinks; when
# scores are flat, the set stays wide.
RELATIVE_SCORE_FLOOR = 0.45

# Below this confidence the router admits it does not know and widens
# the selection instead of guessing.
FALLBACK_CONFIDENCE = 0.35

# How much of the previous turn's service context carries forward when
# the current message names no service at all ("and delete it").
CONTEXT_CARRY_OVER = 0.70


class ToolRouter:
    """
    Selects the tools relevant to a user request.

    Stateless with respect to conversations: `route()` takes everything
    it needs as arguments and returns a value. The only state is the
    cached index, which is derived from the registry.

    That matters for Phase 3. A FastAPI backend serves many users
    concurrently; a router holding per-conversation state would need
    locking, and would leak one user's context into another's request.
    """

    __slots__ = (
        "registry",
        "_embeddings",
        "_index",
        "_index_version",
        "_tool_vectors",
    )

    def __init__(
        self,
        registry: ToolRegistry,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:

        self.registry = registry

        # Null object, never None - see embeddings.py for why.
        self._embeddings: EmbeddingProvider = (
            embedding_provider or NullEmbeddingProvider()
        )

        self._index: ToolIndex | None = None
        self._index_version = -1
        self._tool_vectors: dict[str, list[float]] = {}

    # -----------------------------------------------------------------
    # Index lifecycle
    # -----------------------------------------------------------------

    @property
    def index(self) -> ToolIndex:
        """
        The current index, rebuilt only when the registry changed.

        Cache invalidation by version counter rather than by rebuilding
        on every call. The registry bumps its version on every
        mutation, so this is correct without the router having to
        subscribe to anything - and a stale index is impossible.
        """

        version = self.registry.version

        if self._index is None or version != self._index_version:

            self._index = ToolIndex(self.registry.all())
            self._index_version = version
            self._tool_vectors = {}

        return self._index

    # -----------------------------------------------------------------
    # Query understanding
    # -----------------------------------------------------------------

    @staticmethod
    def detect_intent(raw_tokens: list[str]) -> Operation | None:
        """
        What kind of action the user is asking for.

        The FIRST recognized verb wins. English imperatives lead with
        the action - "show me my open issues" starts with "show", and
        the later word "open" (a write verb) is a red herring. Scanning
        left to right handles that correctly and is easy to reason
        about, which matters more than cleverness for a safety signal.
        """

        for token in raw_tokens:
            for operation, verbs in INTENT_VERBS.items():
                if token in verbs:
                    return operation

        return None

    @staticmethod
    def _phrase_score(alias: str, raw_tokens: list[str]) -> float:
        """
        Match a multi-word alias like "pull request" or "google drive".

        Slides a window the length of the alias across the query and
        fuzzy-matches the joined text, so "pul reqest" still lands.
        Single-token matching alone would miss these entirely, because
        neither "pull" nor "request" on its own is decisive.
        """

        alias_words = alias.split()
        width = len(alias_words)

        if len(raw_tokens) < width:
            return 0.0

        best = 0.0

        for start in range(len(raw_tokens) - width + 1):

            window = " ".join(raw_tokens[start:start + width])

            score = fuzzy_ratio(window, alias)

            if score > best:
                best = score

        return best

    def score_namespaces(
        self,
        raw_tokens: list[str],
        content_tokens: list[str],
    ) -> list[NamespaceScore]:
        """
        Score every service against the query.

        Note what this does NOT do: pick a winner. It returns scores for
        all services and lets the caller decide how many to keep.
        "Find the GitHub issue and check whether it was discussed in
        Slack" is one request that legitimately needs two services, and
        any design that returns a single namespace makes that request
        impossible to serve.
        """

        scores: list[NamespaceScore] = []

        for namespace, aliases in NAMESPACE_ALIASES.items():

            best = 0.0
            matched: list[str] = []

            for alias in aliases:

                if " " in alias:
                    score = self._phrase_score(alias, raw_tokens)
                else:
                    score = best_match(alias, content_tokens)

                if score >= ALIAS_MATCH_THRESHOLD:

                    matched.append(alias)

                    if score > best:
                        best = score

            if not matched:
                continue

            # Independent agreement raises confidence.
            bonus = min(
                ALIAS_AGREEMENT_CAP,
                ALIAS_AGREEMENT_BONUS * (len(matched) - 1),
            )

            scores.append(
                NamespaceScore(
                    namespace=namespace,
                    score=min(1.0, best + bonus),
                    matched_aliases=tuple(sorted(matched)),
                )
            )

        scores.sort(key=lambda item: item.score, reverse=True)

        return scores

    # -----------------------------------------------------------------
    # Scoring signals
    # -----------------------------------------------------------------

    @staticmethod
    def operation_alignment(
        intent: Operation | None,
        operation: Operation,
    ) -> float:
        """
        How well a tool's kind of action matches what the user asked.

        The asymmetry in the middle of this function is the important
        part, and it is easy to get wrong in a way that quietly breaks
        multi-step agents:

          READ intent + mutating tool  -> 0.0
              "show me my files" should not surface delete_file.

          WRITE intent + READ tool     -> 0.6, NOT 0.0
              To send a Slack message to John, the agent must FIRST
              call slack_list_users to resolve "John" into a user id.
              A router that strips read tools from write requests makes
              every realistic multi-step task fail, because the lookup
              step is missing. This case is the one people get wrong.
        """

        if intent is None:
            # No verb detected. Stay neutral rather than guess.
            return 0.5

        if intent is operation:
            return 1.0

        if intent is Operation.READ:
            return 0.0

        if operation is Operation.READ:
            # Reads are prerequisites for writes.
            return 0.6

        # Both mutate, but not in the same way (e.g. asked to update,
        # tool deletes). Plausible, but not what was asked for.
        return 0.35

    @staticmethod
    def executability(
        tool: ToolDefinition,
        query_terms: set[str],
    ) -> float:
        """
        How likely the agent is to be able to actually RUN this tool.

        A tool the model cannot call is worth less than an equally
        relevant tool it can. This came out of a real failure:

            User: "check github drive slack and calander connection"

            google_calendar_freebusy        needs time_min, time_max
                                            and calendar_ids
            google_calendar_list_calendars  needs nothing

        Both are equally "about" the calendar. Only one can answer the
        question. The router picked the wrong one, the model invented
        arguments, and validation rejected the call three times.

        The rule: count required arguments the query gives no hint
        about, and apply a small penalty for each.

            no required arguments        -> 1.00
            one unfilled required arg    -> 0.71
            three unfilled required args -> 0.45

        An argument counts as "hinted" when its name appears in the
        query - "search files for the budget REPORT" hints at a `query`
        parameter. That is a rough test, and it is meant to be. This is
        a tiebreaker at weight 0.06, not a gate: a tool with three
        required arguments still wins easily if the topic matches and
        the alternatives do not.
        """

        schema = tool.input_schema

        if not isinstance(schema, dict):
            return 1.0

        required = schema.get("required")

        if not isinstance(required, list) or not required:
            return 1.0

        unfilled = 0

        for name in required:

            if not isinstance(name, str):
                continue

            hinted = any(
                fuzzy_ratio(part, term) > 0.0
                for part in name.lower().split("_")
                if len(part) > 2
                for term in query_terms
            )

            if not hinted:
                unfilled += 1

        unfilled = min(unfilled, MAX_COUNTED_UNFILLED_ARGUMENTS)

        return 1.0 / (1.0 + UNFILLED_ARGUMENT_PENALTY * unfilled)

    def _semantic_scores(
        self,
        query: str,
        tools: list[ToolDefinition],
    ) -> dict[str, float]:
        """
        Optional embedding similarity. Empty dict when disabled.
        """

        if not self._embeddings.is_enabled:
            return {}

        missing = [
            tool
            for tool in tools
            if tool.name not in self._tool_vectors
        ]

        if missing:

            vectors = self._embeddings.embed([
                f"{tool.name}. {tool.description or ''}"
                for tool in missing
            ])

            for tool, vector in zip(missing, vectors):
                self._tool_vectors[tool.name] = vector

        query_vectors = self._embeddings.embed([query])

        if not query_vectors or not query_vectors[0]:
            return {}

        query_vector = query_vectors[0]

        return {
            tool.name: cosine_similarity(
                query_vector,
                self._tool_vectors.get(tool.name, []),
            )
            for tool in tools
        }

    @staticmethod
    def _active_weights(semantic_enabled: bool) -> dict[str, float]:
        """
        Weights for the signals actually in play, normalized to sum 1.

        Without renormalization, turning the semantic layer off would
        cap every score at 0.90 and silently shift the meaning of every
        threshold in this file. Normalizing keeps scores comparable
        whether or not embeddings are configured.
        """

        weights = {
            "lexical": WEIGHT_LEXICAL,
            "namespace": WEIGHT_NAMESPACE,
            "operation": WEIGHT_OPERATION,
            "executability": WEIGHT_EXECUTABILITY,
        }

        if semantic_enabled:
            weights["semantic"] = WEIGHT_SEMANTIC

        total = sum(weights.values())

        return {
            key: value / total
            for key, value in weights.items()
        }

    # -----------------------------------------------------------------
    # Selection
    # -----------------------------------------------------------------

    @staticmethod
    def _select_fairly(
        ranked: list[ToolCandidate],
        selected_namespaces: list[str],
        top_k: int,
    ) -> list[ToolCandidate]:
        """
        Pick the final tools, guaranteeing EVERY selected service gets a
        usable set - not just the top-scoring one.

        A plain global top-K looks correct and fails badly on exactly
        the request Phase2.md calls its most important milestone:

            "Find the GitHub authentication issue and check whether
             anyone discussed it in Slack."

        GitHub has 18 tools and a rich vocabulary; Slack has 14 and
        terse descriptions. Sorted purely by score, the top 10 can
        easily be 10 GitHub tools - and the agent then cannot check
        Slack, because it was never given a Slack tool to call. The
        request fails for a reason the LLM cannot see or recover from.

        Services are processed BEST FIRST, so when the budget is tight
        the strongest service is served first and the weakest one is
        the one that gets squeezed - never the other way round.

        Reserving slots per service costs a little precision and buys
        the entire multi-service capability.
        """

        if not ranked:
            return []

        selected: list[ToolCandidate] = []
        taken: set[str] = set()

        # Pass 1: reserved slots, best-first within each service.
        for namespace in selected_namespaces:

            quota = MIN_TOOLS_PER_NAMESPACE

            for candidate in ranked:

                if quota <= 0:
                    break

                if candidate.namespace != namespace:
                    continue

                if candidate.tool_name in taken:
                    continue

                selected.append(candidate)
                taken.add(candidate.tool_name)
                quota -= 1

        # Pass 2: fill what remains by pure score.
        for candidate in ranked:

            if len(selected) >= top_k:
                break

            if candidate.tool_name in taken:
                continue

            selected.append(candidate)
            taken.add(candidate.tool_name)

        selected.sort(key=lambda item: item.score, reverse=True)

        return selected[:top_k]

    # -----------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------

    def route(
        self,
        query: str,
        *,
        top_k: int | None = None,
        previous_namespaces: tuple[str, ...] | None = None,
        exclude: set[str] | None = None,
    ) -> RoutingDecision:
        """
        Choose the tools for one user message.

        `top_k` defaults to None, meaning "decide for me". The budget
        then scales with how many services the request touches: 8 tools
        for one service, 20 for four. Pass a number to override.

        `previous_namespaces` carries the last turn's services forward.
        Real conversations are full of messages that name no service at
        all - "and delete that one", "show me more", "what about
        tomorrow". Routed in isolation those match nothing and fall
        back to all 61 tools. Given the previous context they route
        correctly. This is the routing half of the same problem your
        system prompt already addresses for the LLM.

        `exclude` removes tools from consideration entirely. This is
        what makes a SECOND routing pass useful: when the first set of
        tools turns out not to work, the agent can ask for a different
        set rather than being handed the same failures again. See
        `agent/loop.py` for the escalation that uses it.
        """

        started = time.perf_counter()

        index = self.index

        raw_tokens = tokenize(query)

        content_tokens = [
            normalize_token(token)
            for token in raw_tokens
            if token not in STOPWORDS
        ]

        # Intent runs on singularized tokens so "whats" still resolves
        # to the verb "what". Stopwords are NOT removed first: filler
        # words are irrelevant here, but verbs are the whole signal.
        intent = self.detect_intent([
            normalize_token(token)
            for token in raw_tokens
        ])

        # --- Which services? -----------------------------------------

        namespace_scores = self.score_namespaces(
            raw_tokens,
            content_tokens,
        )

        carried_over = False

        if not namespace_scores and previous_namespaces:

            carried_over = True

            namespace_scores = [
                NamespaceScore(
                    namespace=namespace,
                    score=CONTEXT_CARRY_OVER,
                    matched_aliases=("carried from previous turn",),
                )
                for namespace in previous_namespaces
            ]

        pooled: list[str] = []

        if namespace_scores:

            top_score = namespace_scores[0].score

            for entry in namespace_scores[:MAX_NAMESPACES]:

                if entry.score < top_score - NAMESPACE_POOL_MARGIN:
                    break

                pooled.append(entry.namespace)

        namespace_lookup = {
            entry.namespace: entry.score
            for entry in namespace_scores
        }

        # The tool budget grows with the number of services involved.
        # One service needs 8 tools; four services need 20, or each one
        # gets too few to be usable.
        if top_k is None:
            top_k = min(
                MAX_TOP_K,
                BASE_TOP_K
                + TOP_K_PER_EXTRA_SERVICE * max(0, len(pooled) - 1),
            )

        # --- Which tools? --------------------------------------------

        expansions = index.expand(content_tokens)

        # Command verbs are grammar, not topic - scale their weight down
        # so a word like "check" cannot out-rank the word "calendar"
        # just because it happens to appear in one docstring.
        expansions = [
            replace(
                expansion,
                weight=expansion.weight * INTENT_VERB_WEIGHT_SCALE,
            )
            if expansion.token in ALL_INTENT_VERBS
            else expansion
            for expansion in expansions
        ]

        # Report only tokens that SHOULD have matched. A verb like
        # "show" is consumed by intent detection and is not expected to
        # appear in any tool's vocabulary, so listing it here would
        # bury the signal we actually want: real nouns the lexicon does
        # not know yet.
        unmatched = tuple(
            expansion.token
            for expansion in expansions
            if expansion.is_out_of_vocabulary
            and expansion.token not in ALL_INTENT_VERBS
        )

        # The pool is the union of two sources:
        #   - every tool in a service the query pointed at
        #   - every tool with a direct term match, whatever its service
        #
        # The second source matters: "who am I?" names no service, but
        # the keyword lists connect it to both
        # github_get_authenticated_user and slack_auth_info.
        candidate_names = index.candidate_tool_names(expansions)

        pool: list[ToolDefinition] = [
            tool
            for tool in index.tools
            if (
                tool.namespace in pooled
                or tool.name in candidate_names
            )
            and (exclude is None or tool.name not in exclude)
        ]

        if not pool:
            return self._fallback(
                query=query,
                intent=intent,
                namespace_scores=namespace_scores,
                unmatched=unmatched,
                top_k=top_k,
                started=started,
            )

        # Safety rule: a read-shaped request never surfaces
        # irreversible tools. Deleting and re-sharing cannot be undone,
        # so we would rather lose recall than hand the model a loaded
        # weapon it was never asked to pick up. WRITE tools stay - they
        # are approval-gated downstream, and a read often precedes one.
        if intent is Operation.READ:
            pool = [
                tool
                for tool in pool
                if tool.operation
                not in (Operation.DELETE, Operation.ADMIN)
            ]

        semantic = self._semantic_scores(query, pool)

        weights = self._active_weights(bool(semantic))

        # Every word the user actually typed, for the executability
        # check. Built once, not per tool.
        query_terms = set(content_tokens)

        # How strongly the user pointed at a service at all. Zero when
        # the query named none, which switches the cross-service
        # penalty off entirely.
        namespace_confidence = (
            namespace_scores[0].score
            if namespace_scores
            else 0.0
        )

        cross_service_factor = 1.0 - (
            CROSS_SERVICE_PENALTY * namespace_confidence
        )

        ranked: list[ToolCandidate] = []

        for tool in pool:

            lexical = index.lexical_score(tool, expansions)

            namespace_prior = namespace_lookup.get(
                tool.namespace or "",
                0.0,
            )

            alignment = self.operation_alignment(
                intent,
                tool.operation,
            )

            runnable = self.executability(tool, query_terms)

            semantic_score = semantic.get(tool.name, 0.0)

            final = (
                weights["lexical"] * lexical
                + weights["namespace"] * namespace_prior
                + weights["operation"] * alignment
                + weights["executability"] * runnable
                + weights.get("semantic", 0.0) * semantic_score
            )

            reasons: list[str] = []

            # A tool from a service the user did not ask for has to
            # clear a much higher bar than one from the service they
            # named.
            if pooled and tool.namespace not in pooled:
                final *= cross_service_factor
                reasons.append("outside the requested service")

            if lexical > 0.0:
                reasons.append(f"lexical match {lexical:.2f}")

            if namespace_prior > 0.0:
                reasons.append(f"service {tool.namespace}")

            if runnable < 1.0:
                reasons.append("needs unsupplied arguments")

            if intent is not None and alignment >= 1.0:
                reasons.append(f"intent {intent.value}")

            if carried_over:
                reasons.append("context from previous turn")

            ranked.append(
                ToolCandidate(
                    tool_name=tool.name,
                    namespace=tool.namespace,
                    score=final,
                    breakdown=ScoreBreakdown(
                        lexical=lexical,
                        namespace=namespace_prior,
                        operation=alignment,
                        executability=runnable,
                        semantic=semantic_score,
                    ),
                    reasons=tuple(reasons),
                    tool=tool,
                )
            )

        ranked.sort(key=lambda item: item.score, reverse=True)

        # Relative floor: keep only what is competitive with the best.
        best_score = ranked[0].score if ranked else 0.0
        floor = best_score * RELATIVE_SCORE_FLOOR

        ranked = [
            candidate
            for candidate in ranked
            if candidate.score >= floor
        ]

        selected = self._select_fairly(ranked, pooled, top_k)

        # --- How sure are we? ----------------------------------------

        namespace_confidence = (
            namespace_scores[0].score
            if namespace_scores
            else 0.0
        )

        tool_confidence = selected[0].score if selected else 0.0

        confidence = (
            0.4 * namespace_confidence
            + 0.6 * tool_confidence
        )

        if confidence < FALLBACK_CONFIDENCE or not selected:
            return self._fallback(
                query=query,
                intent=intent,
                namespace_scores=namespace_scores,
                unmatched=unmatched,
                top_k=top_k,
                started=started,
            )

        return RoutingDecision(
            candidates=tuple(selected),
            confidence=min(1.0, confidence),
            namespaces=tuple(pooled),
            namespace_scores=tuple(namespace_scores),
            fallback_used=False,
            intent=intent,
            unmatched_tokens=unmatched,
            query=query,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    # -----------------------------------------------------------------
    # Fallback
    # -----------------------------------------------------------------

    def _fallback(
        self,
        *,
        query: str,
        intent: Operation | None,
        namespace_scores: list[NamespaceScore],
        unmatched: tuple[str, ...],
        top_k: int,
        started: float,
    ) -> RoutingDecision:
        """
        What to do when the router does not know.

        It returns MORE tools, not fewer.

        This is counter-intuitive until you look at the two failure
        modes side by side:

          Too many tools  -> the LLM pays extra tokens and may pick a
                             slightly suboptimal tool. Recoverable; the
                             agent loop gets another turn.

          Too few tools   -> the LLM cannot call the tool it needs,
                             because it was never told the tool exists.
                             It then hallucinates an answer or gives
                             up. Unrecoverable, and it looks like the
                             model is broken when the router is.

        Given that asymmetry, uncertainty should always resolve towards
        breadth. `fallback_used` is set so this is visible in logs
        rather than silent - a rising fallback rate is your signal that
        the lexicon needs new aliases.
        """

        index = self.index

        tools = list(index.tools)

        if intent is Operation.READ:
            tools = [tool for tool in tools if tool.read_only]

        # Widened, but still bounded. Read-only requests fan out more
        # because they are safe.
        limit = max(top_k * 2, 20)

        candidates = tuple(
            ToolCandidate(
                tool_name=tool.name,
                namespace=tool.namespace,
                score=0.0,
                reasons=("fallback: low routing confidence",),
                tool=tool,
            )
            for tool in tools[:limit]
        )

        return RoutingDecision(
            candidates=candidates,
            confidence=0.0,
            namespaces=tuple(
                entry.namespace
                for entry in namespace_scores
            ),
            namespace_scores=tuple(namespace_scores),
            fallback_used=True,
            intent=intent,
            unmatched_tokens=unmatched,
            query=query,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    # -----------------------------------------------------------------
    # Deterministic routing (kept from Phase 2.3.1)
    # -----------------------------------------------------------------

    def route_by_namespace(
        self,
        namespaces: list[str],
    ) -> RoutingDecision:
        """
        Return every tool in the given services, unranked.

        Still useful, and not dead code: tests use it to build a known
        tool set, and Phase 3's agent builder needs it for "this agent
        may use GitHub and Drive" - a configuration choice, not a
        per-message routing decision.
        """

        wanted = {
            namespace.lower().strip()
            for namespace in namespaces
        }

        candidates = tuple(
            ToolCandidate(
                tool_name=tool.name,
                namespace=tool.namespace,
                score=1.0,
                reasons=("namespace match",),
                tool=tool,
            )
            for tool in self.index.tools
            if tool.namespace in wanted
        )

        return RoutingDecision(
            candidates=candidates,
            confidence=1.0 if candidates else 0.0,
            namespaces=tuple(sorted(wanted)),
            fallback_used=False,
        )
