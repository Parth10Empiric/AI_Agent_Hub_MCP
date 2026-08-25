from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .schemas import ToolDefinition
from .text import fuzzy_ratio, normalize_token

"""
The search index (Phase 2.3).

A router that compares every query token against every term of every
tool is O(tokens x tools x terms). With 161 tools that is survivable;
with the 300+ tools the Agent Hub roadmap plans for, it is not.

This module builds an inverted index once at discovery time, so a
query only ever scores the handful of tools that could plausibly match.

Two ideas do all the work here:

  1. Inverted index (term -> tools that contain it).
     Instead of asking "does this tool match?" 161 times, we ask "which
     tools contain a term like this?" once.

  2. IDF weighting (inverse document frequency).
     The token "list" appears in ~25 of your tools, so it tells us
     almost nothing. "freebusy" appears in one, so it is decisive.
     Weighting tokens by rarity is what stops common filler words from
     dominating the score.
"""


# How much a match counts, depending on where in the tool it was found.
#
# A hit in the tool NAME is the strongest evidence: names are curated
# and short. A hit in the description is weaker because descriptions
# contain incidental words. Keywords sit in between - they are
# hand-written routing vocabulary, so they are trustworthy, but they
# are looser than the name itself.
FIELD_WEIGHT_NAME = 1.00
FIELD_WEIGHT_KEYWORD = 0.85
FIELD_WEIGHT_DESCRIPTION = 0.65


# The rarest a term is allowed to be treated as, as a fraction of the
# corpus.
#
# Plain IDF is log(1 + N / (1 + df)). For a term in exactly ONE tool
# that is log(1 + N/2), which GROWS without limit as the server gains
# tools - so the more tools you add, the more decisive an incidental
# word in a single docstring becomes. That is backwards, and it is a
# real failure rather than a theoretical one:
#
#   "...send a short summary with the issue titles and links to
#    #mcp_test on Slack"
#
# The words "title" and "link" are ordinary English, not domain terms.
# Each happened to appear in exactly one Slack docstring, so IDF made
# them decisive, and slack_get_user_profile ("...title, status and
# contact fields") and slack_get_permalink ("...a shareable link...")
# both outranked slack_send_message - which is the one tool that
# sentence is actually asking for.
#
# Clamping df from below says: below this frequency, we cannot tell
# "genuinely distinctive" from "somebody's turn of phrase", so stop
# pretending we can. Expressed as a FRACTION rather than a count so
# the ceiling is the same at 60 tools and at 600 - the whole point is
# that growing the server must not silently re-tune the scorer.
#
# This does not make rare terms weak. At this floor a distinctive term
# is still worth roughly twice a term appearing in half the corpus.
# It only stops one accidental word being worth four of them.
IDF_FLOOR_FRACTION = 0.15


@dataclass(frozen=True, slots=True)
class TokenExpansion:
    """
    One query token, resolved against the index vocabulary.

    The matches map records how well the token matched each term:

        token "githb" maps to {"github": 0.83}
        token "isue"  maps to {"issue": 0.80}

    This is where typo tolerance actually lands. By the time scoring
    runs, the misspelling has already been resolved into real indexed
    terms, so the rest of the router never has to think about typos.
    """

    token: str
    weight: float
    matches: dict[str, float]


    @property
    def is_out_of_vocabulary(self) -> bool:
        return not self.matches


class ToolIndex:
    """
    Immutable search index over a set of tools.

    Rebuilt whenever the registry changes, never mutated in place.
    """

    __slots__ = (
        "_tools",
        "_by_name",
        "_vocabulary",
        "_idf",
        "_postings",
        "_namespaces",
        "_service_terms",
    )

    def __init__(self, tools: list[ToolDefinition]) -> None:

        self._tools: tuple[ToolDefinition, ...] = tuple(tools)

        self._by_name: dict[str, ToolDefinition] = {
            tool.name: tool
            for tool in self._tools
        }

        # term -> set of tool names containing it (in any field)
        self._postings: dict[str, set[str]] = defaultdict(set)

        # term -> inverse document frequency
        self._idf: dict[str, float] = {}

        self._vocabulary: tuple[str, ...] = ()
        self._namespaces: tuple[str, ...] = ()
        self._service_terms: frozenset[str] = frozenset()

        self._build()

    # -----------------------------------------------------------------
    # Construction
    # -----------------------------------------------------------------

    def _build(self) -> None:

        document_frequency: dict[str, int] = defaultdict(int)

        namespaces: set[str] = set()

        # Name terms shared by EVERY tool in a namespace, collected per
        # namespace and intersected below. See `service_terms`.
        name_terms_by_namespace: dict[str, set[str]] = {}

        for tool in self._tools:

            if tool.namespace:
                namespaces.add(tool.namespace)

                shared = name_terms_by_namespace.get(tool.namespace)
                terms = set(tool.name_terms)

                if shared is None:
                    name_terms_by_namespace[tool.namespace] = terms
                else:
                    shared &= terms

            # A term is counted ONCE per tool even if it appears in the
            # name and the description. Otherwise a tool that repeats a
            # word would inflate that term's document frequency and
            # wrongly make it look common across the whole corpus.
            unique_terms = set(tool.search_terms)

            for term in unique_terms:
                document_frequency[term] += 1
                self._postings[term].add(tool.name)

        total_documents = max(len(self._tools), 1)

        # Nothing is treated as rarer than this many documents. See
        # IDF_FLOOR_FRACTION for why the floor scales with the corpus.
        frequency_floor = IDF_FLOOR_FRACTION * total_documents

        for term, frequency in document_frequency.items():
            # Smoothed IDF. The inner +1 keeps the value finite when a
            # term appears in every document, and the outer +1 keeps it
            # positive so a common term is down-weighted rather than
            # zeroed out entirely.
            effective = max(float(frequency), frequency_floor)

            self._idf[term] = math.log(
                1.0 + (total_documents / (1.0 + effective))
            )

        self._vocabulary = tuple(sorted(document_frequency))

        self._namespaces = tuple(sorted(namespaces))

        self._service_terms = frozenset(
            term
            for terms in name_terms_by_namespace.values()
            for term in terms
        )

    # -----------------------------------------------------------------
    # Accessors
    # -----------------------------------------------------------------

    @property
    def tools(self) -> tuple[ToolDefinition, ...]:
        return self._tools

    @property
    def vocabulary(self) -> tuple[str, ...]:
        return self._vocabulary

    @property
    def namespaces(self) -> tuple[str, ...]:
        return self._namespaces

    @property
    def service_terms(self) -> frozenset[str]:
        """
        Terms that identify a SERVICE rather than a capability.

        Derived, not listed: a term qualifies when it appears in the
        name of every single tool of some namespace - "github",
        "google", "drive", "slack", "calendar". Such a term cannot
        distinguish two tools of that service from each other, and
        between services it says exactly what the namespace prior
        already says.

        Scoring it lexically as well is the same double counting the
        router already corrects for command verbs, and it gets worse
        as the server grows: IDF measures rarity across the whole
        corpus, so every tool added to a service makes that service's
        own name look more common and therefore less informative.
        Adding 29 Slack tools measurably pushed Slack's tools down the
        ranking for the query "check ... slack ... connection" - the
        service was penalised for having more to offer.

        Because it is computed from the tools themselves, removing
        tools re-derives it too. Nothing here needs editing when the
        tool set changes.
        """

        return self._service_terms

    def get(self, tool_name: str) -> ToolDefinition | None:
        return self._by_name.get(tool_name)

    def idf(self, term: str) -> float:
        """
        Rarity weight for a term. Unknown terms get the mean weight.
        """

        if term in self._idf:
            return self._idf[term]

        if not self._idf:
            return 1.0

        return sum(self._idf.values()) / len(self._idf)

    # -----------------------------------------------------------------
    # Query expansion
    # -----------------------------------------------------------------

    def expand(self, tokens: list[str]) -> list[TokenExpansion]:
        """
        Resolve each query token into the indexed terms it matches.

        This is the single most expensive step in routing, and it is
        deliberately done ONCE per query rather than once per tool.
        Scoring 161 tools afterwards is then just dictionary lookups.

        fuzzy_ratio is cached, so repeated queries and repeated tokens
        across a conversation cost almost nothing.
        """

        expansions: list[TokenExpansion] = []

        for token in tokens:

            normalized = normalize_token(token)

            matches: dict[str, float] = {}

            for term in self._vocabulary:

                score = fuzzy_ratio(normalized, term)

                if score > 0.0:
                    matches[term] = score

            # Weight the token by the rarity of the best term it
            # resolved to. A typo of a rare word is still rare.
            if matches:
                best_term = max(matches, key=matches.__getitem__)
                weight = self.idf(best_term)
            else:
                weight = 0.0

            expansions.append(
                TokenExpansion(
                    token=normalized,
                    weight=weight,
                    matches=matches,
                )
            )

        return expansions

    def candidate_tool_names(
        self,
        expansions: list[TokenExpansion],
    ) -> set[str]:
        """
        Every tool that contains at least one matched term.

        Tools outside this set scored zero on every token, so there is
        no reason to run the scoring loop over them at all.
        """

        candidates: set[str] = set()

        for expansion in expansions:
            for term in expansion.matches:
                candidates.update(self._postings.get(term, ()))

        return candidates

    # -----------------------------------------------------------------
    # Per-tool scoring
    # -----------------------------------------------------------------

    def field_score(
        self,
        tool: ToolDefinition,
        expansion: TokenExpansion,
    ) -> float:
        """
        How strongly one query token matches one tool, 0.0 to 1.0.

        The token is compared against each field separately and the
        best field wins, scaled by that field's trust weight. Taking
        the max rather than the sum matters: a tool should not rank
        higher merely because the same word appears in both its name
        and its description.
        """

        matches = expansion.matches

        if not matches:
            return 0.0

        def best(terms: tuple[str, ...]) -> float:
            best_score = 0.0
            for term in terms:
                score = matches.get(term, 0.0)
                if score > best_score:
                    best_score = score
            return best_score

        return max(
            best(tool.name_terms) * FIELD_WEIGHT_NAME,
            best(tool.keyword_terms) * FIELD_WEIGHT_KEYWORD,
            best(tool.description_terms) * FIELD_WEIGHT_DESCRIPTION,
        )

    def lexical_score(
        self,
        tool: ToolDefinition,
        expansions: list[TokenExpansion],
    ) -> float:
        """
        Overall lexical relevance of a tool to the whole query.

        A weighted average, not a sum. A sum rewards long queries and
        makes scores incomparable between a three-word question and a
        twenty-word one; an average keeps every score on the same
        0.0 to 1.0 scale so a single fixed threshold works everywhere.

        Out-of-vocabulary tokens contribute zero weight, so a
        repository name or a person's name in the query does not
        dilute every tool's score equally.
        """

        total_weight = 0.0
        accumulated = 0.0

        for expansion in expansions:

            weight = expansion.weight

            if weight <= 0.0:
                continue

            total_weight += weight
            accumulated += weight * self.field_score(tool, expansion)

        if total_weight <= 0.0:
            return 0.0

        return accumulated / total_weight
