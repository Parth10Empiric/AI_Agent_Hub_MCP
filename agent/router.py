from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from .embeddings import (
    EmbeddingProvider,
    NullEmbeddingProvider,
    cosine_similarity,
)
from .index import TokenExpansion, ToolIndex
from .lexicon import (
    ALL_INTENT_VERBS,
    INTENT_VERBS,
    NAMESPACE_ALIASES,
    STOPWORDS,
    TASK_EXPANSIONS,
    expansions_for_task,
)
from .registry import ToolRegistry
from .routing import (
    NamespaceScore,
    RoutingDecision,
    ScoreBreakdown,
    ToolCandidate,
)
from .schemas import Operation, ToolDefinition
from .text import (
    SCORE_TYPO_FLOOR,
    best_match,
    fuzzy_ratio,
    normalize_token,
    tokenize,
)

"""
Hybrid tool router (Phase 2.3).

The job: turn a sentence a human typed into the small set of tools the
LLM should be allowed to see for that turn.

Why not just send all 161 tools every time?

  - Cost. Tool schemas are tokens. 161 schemas is a large fixed tax on
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

# Raised from 0.08, which was too small to ever decide anything.
#
# On the query that motivated most of this file's recent changes, the
# top eleven candidates were spread across 0.06 of score. A signal
# weighted at 0.08 can move a tool by at most 0.08 even when it is
# completely certain, so at the old weight the semantic layer could
# not have broken that tie no matter how right it was.
#
# It is still the smallest weight of the five, and that is correct: it
# is the only signal that comes from a black box.
WEIGHT_SEMANTIC = 0.18

# Above this, the best lexical score means the user typed words the
# tool corpus actually uses, and keyword ranking can be trusted.
#
# Measured, not guessed. Best lexical score by query:
#
#     "list my github issues"           1.00   named the tool
#     "send a message to the channel"   0.85   named the tool
#     "tell me about my workspace"      0.68   named the resource
#     "what is this project built with" 0.45   named NOTHING a tool
#     "repo summary please"             0.44   uses in its own text
#
# The gap between those two groups is the whole signal. A request
# phrased in the tool's vocabulary is ranked well by keywords and
# semantics cannot improve on it; a request phrased in the user's own
# terms leaves keyword scoring guessing, and that is where meaning has
# something to add.
LEXICAL_DECISIVE = 0.55

# WHY THIS IS A THRESHOLD ON THE BEST SCORE AND NOT A TEST FOR A TIE
#
# The obvious reading of the failure is "eleven tools tied, so detect
# ties" - and a tie test was written first. It does not work, because
# a tie is not evidence of anything on its own:
#
#     "list my github issues"     1.00, 1.00, 0.87, 0.87, 0.87, 0.87
#     "repo summury provide me"   0.275, 0.188, 0.188, 0.188, 0.188
#
# Both are bunched. The first is six issue tools agreeing that this is
# a question about issues, which is a correct answer arrived at
# confidently; the second is eleven unrelated tools sharing the word
# "repo". Tie detection fires on both, spends an embedding call on the
# first, and improves nothing.
#
# What separates them is the HEIGHT of the cluster, not its width. A
# cluster at 0.87 means several tools genuinely match the words; a
# cluster at 0.19 means the words matched nothing much and the
# ordering is whatever fell out.

# How much the semantic weight grows when lexical goes flat. Applied
# before normalization, so the other four signals shrink to make room.
SEMANTIC_FLAT_BOOST = 3.0

# How much a command verb counts as topical evidence.
#
# This exists because of a real failure. IDF is computed over only 161
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

# How much a SERVICE NAME counts as topical evidence.
#
# The same double-counting argument as above, on the other axis.
# "github", "slack", "drive", "calendar" and "google" appear in the
# name of every tool of their service (ToolIndex.service_terms derives
# exactly that set), so they cannot separate two tools WITHIN a
# service, and BETWEEN services they repeat what the namespace prior
# has already said at WEIGHT_NAMESPACE.
#
# Left at full weight it also scales badly, in the wrong direction.
# IDF measures rarity across the whole corpus, so every tool added to
# a service makes that service's own name look more common and
# therefore less informative - and the service is quietly penalised
# for having grown. Going from 14 Slack tools to 43 pushed Slack far
# enough down the ranking of "check github drive slack and calander
# connection" that five Google Drive tools requiring a file_id nobody
# had supplied outranked Slack's argument-free ones.
#
# Down-weighted rather than dropped, for the same reason as the verbs:
# a query that names ONLY a service ("slack?") has nothing else to go
# on, and a tiebreaker is still worth having.
SERVICE_TERM_WEIGHT_SCALE = 0.25

# How much a term the user did not type, added by a task expansion,
# counts against the words they did type.
#
# Below 1.0 on purpose. "Summarise the repo" really does imply reading
# the readme, but the user said "repo" and only implied "readme", and a
# system that treats the two as equal will eventually let an inference
# outrank a fact. Sixty percent is enough to break a tie between eleven
# equally-scored tools - which is the entire job - and not enough to
# beat a tool the user named outright.
TASK_EXPANSION_WEIGHT_SCALE = 0.6

# Minimum similarity for a misspelled word to count as an intent verb.
#
# Intent detection was the one exact-match test left in an otherwise
# typo-tolerant router, and it showed: "summury provide me" produced no
# intent at all, so every tool - including github_delete_repository -
# scored a neutral 0.5 on operation alignment for what was plainly a
# read.
#
# The same floor the rest of the router uses, guarded differently.
#
# A false positive here is worse than one anywhere else in the file:
# elsewhere a wrong fuzzy match adds a bad candidate, here it changes
# what KIND of action the router believes was requested, which decides
# whether destructive tools are filtered out at all. The first attempt
# raised the threshold to 0.88 to buy that safety - and it bought too
# much, rejecting the very typo it was written for ("summury" scores
# 0.857 against "summary").
#
# The guard that actually works is LENGTH, applied in
# `_verb_operation`: both words must be five characters or more. Below
# that, text.py's edit budget is zero and its prefix rule fires on
# three shared letters, which is where verb confusion actually comes
# from ("read"/"add", "see"/"seed"). Above it, the budget is tight
# enough that no two verbs of DIFFERENT operations fall inside it -
# "update"/"upload" are two edits apart with a budget of one.
#
# A precise guard beats a blunt threshold: this one excludes the
# collisions that exist rather than a fraction of all matches.
FUZZY_VERB_THRESHOLD = SCORE_TYPO_FLOOR

# Words that end one clause and begin another.
#
# Used to give each half of a compound request its own intent. See
# `clause_intents` for why one intent per sentence is not enough.
CLAUSE_SEPARATORS = frozenset({
    "and", "then", "also", "plus", "after", "afterwards",
    "next", "finally", "followed", "meanwhile", "while",
})

# Required arguments that come from ANOTHER TOOL, not from the user.
#
# `executability` penalises a tool for every required argument the
# query gives no hint about. That is right for `time_min` and `query`,
# which a person either says or does not - and exactly backwards for
# the arguments of a second step:
#
#     github_get_file(owner, repo, PATH)     path comes from a listing
#     slack_get_message(channel_id, TS)      ts comes from a history
#     google_drive_read_file(FILE_ID)        id comes from a search
#
# Nobody types a path, a timestamp or a file id, and nobody ever will.
# Counting them as "missing" means the router systematically demotes
# every tool that can only run second - so a multi-step task is scored
# worst on precisely the tools that would complete it. On "summarise
# this repo" that was the difference between offering
# github_list_repositories (needs nothing, answers nothing) and
# github_get_file (needs a path, answers the question).
#
# Matched against the underscore-separated parts of the argument name,
# so `file_id`, `issue_number` and `thread_ts` are all covered without
# listing every service's spelling of the same idea.
CHAINABLE_ARGUMENT_PARTS = frozenset({
    "id", "ids", "sha", "ref", "path", "number", "ts", "timestamp",
    "cursor", "token", "url", "uri", "key", "gid",
})

# Alignment score for a destructive tool on a request that named no
# action at all.
#
# `operation_alignment` used to return a flat 0.5 whenever intent was
# unknown, on the reasoning that a router with no evidence should not
# guess. But "no evidence" is not symmetric between reading and
# deleting: the cost of offering a delete tool nobody asked for is a
# destroyed repository, and the cost of withholding it is one extra
# round in the rare case it was wanted.
#
# Not zero, because `find_tools` and the escalation path both exist to
# recover from exactly this - and because a user who types the word
# "delete" is handled by `mentioned`, above this rule.
UNASKED_DESTRUCTIVE_ALIGNMENT = 0.15

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
# could actually be served, and then to 6, which is a different kind of
# number and worth being explicit about.
#
# It used to be pinned to "the number of services you expose", so the
# day a fifth plugin ships, a request naming five services silently
# loses one - and nothing fails, the agent just cannot do part of the
# job. That is the worst shape a limit can have.
#
# 6 is a bound on how much ONE REQUEST can sensibly touch, not on how
# much the server offers. The real filter is NAMESPACE_POOL_MARGIN,
# which only pools services the query actually pointed at; this is the
# backstop for the pathological case where a message name-drops
# everything. Growing the plugin catalogue no longer requires editing
# it - which is the property that was missing.
MAX_NAMESPACES = 6

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

# Guaranteed slots for a KIND OF ACTION the query named explicitly but
# which was not the leading verb.
#
# The same starvation as above, on a different axis. "list all github
# tools for write or update operations" is read-shaped, so all eight
# slots filled with `list_*` and `search_*` reads and the agent
# reported itself read-only while holding four write tools.
#
# Deliberately smaller than the per-service quota: this is a floor that
# stops a named kind of action vanishing, not a boost that lets it
# outrank the thing the user actually asked for.
MIN_TOOLS_PER_OPERATION = 2

# Guaranteed slots for a READ tool in every selected service.
#
# Reads are the prerequisite step for almost everything, and a single
# sentence-level intent hides that:
#
#     "post a slack summary of my github issues"
#      ^^^^ intent = WRITE, applied to BOTH services
#
# GitHub's three guaranteed slots went to add_issue_comment,
# add_issue_labels and update_file - three ways to write to a service
# the user only wanted to read from. `github_list_issues`, the tool
# that fetches the thing being summarised, was not offered at all, so
# the first half of the task was impossible before the model saw a
# token.
#
# `clause_intents` fixes the common shape of this ("do X and then do
# Y"), but not this one: both services live in the same clause, and
# separating "post ... summary" from "of my github issues" needs a
# parser, not a word list. So the floor stands behind it - whatever the
# router concluded about intent, every service it selected keeps at
# least one way to LOOK at something.
MIN_READ_TOOLS_PER_NAMESPACE = 1

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

# --- Confidence ------------------------------------------------------
#
# Confidence used to be `0.4 * best service + 0.6 * best tool score`,
# which measures how HIGH the winner scored and not whether it actually
# won anything. Those are different questions, and the gap between them
# is where the router failed silently:
#
#     "Student-Faculty-ApplicationReview-System repo summury"
#
#       0.501  github_list_pull_requests
#       0.466  github_list_repositories
#       0.466  github_list_starred_repositories
#       0.449  github_get_repository
#       0.449  github_create_repository       <- eleven candidates
#       0.449  github_delete_repository          within 0.06
#       ...
#
#     reported confidence: 0.70
#
# The ordering there is noise. Every signal that could separate those
# tools was either off (semantic), neutral (no verb detected) or
# identical (all eleven contain "repo"), so the router was choosing at
# random - and said it was 70% sure, which kept `_fallback` from firing
# in the one case it was written for.
#
# Separation is the missing term: how far clear of the field is the
# winner? A tie means "I do not know", regardless of the absolute
# numbers.
#
# Measured against the MEDIAN of the selected set rather than the
# runner-up. Two near-identical tools at the top are common and
# harmless - list_issues and search_issues genuinely both fit - while a
# whole field bunched together is the pathology worth catching.
SEPARATION_TARGET = 0.30

# Confidence retained when separation is zero. Not 0.0: a flat field
# inside a service the user named by name is still better evidence
# than nothing, and driving every tied query into the fallback would
# send 20 tools where 8 would do.
SEPARATION_FLOOR = 0.40

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

    def warm(self) -> int:
        """
        Embed every tool once, up front. Returns how many were cached.

        Called at STARTUP, never from a request. `_semantic_scores`
        embeds any tool it has not seen before, so without this the
        first user message of the process pays for 161 embeddings
        inside a synchronous call inside an async handler - which does
        not slow that one turn down, it blocks the event loop and with
        it every other user.

        Precomputing at startup turns the per-request cost into one
        short query embedding. The vectors are keyed by text, so this
        stays valid until a tool's description changes, and a registry
        change simply re-warms the ones that moved.

        Returns 0 when embeddings are disabled or unreachable, and
        raises nothing. A router that will not start because an
        OPTIONAL ranking signal is unavailable is worse than one that
        ranks slightly less well.
        """

        if not self._embeddings.is_enabled:
            return 0

        tools = list(self.index.tools)

        self._semantic_scores("warm up the embedding cache", tools)

        # Counted by CONTENT, not by key.
        #
        # A failed provider returns one empty vector per tool, which
        # `_semantic_scores` stores faithfully - so counting keys
        # reported 161 embedded tools for a provider that had just
        # failed every one of them, and the startup log said the
        # semantic layer was ready when it was not.
        return sum(
            1
            for vector in self._tool_vectors.values()
            if vector
        )

    # -----------------------------------------------------------------
    # Query understanding
    # -----------------------------------------------------------------

    @staticmethod
    def _verb_operation(token: str) -> Operation | None:
        """
        The kind of action one word names, tolerating a typo.

        EXACT MATCHES ARE TRIED FIRST, ALL OF THEM, before any fuzzy
        one. That ordering is not decoration: "read" is a READ verb and
        "add" is a WRITE verb, and they are one edit apart. Resolving
        exactly-first means a word that IS a verb can never be captured
        by a near-miss on a different one.
        """

        for operation, verbs in INTENT_VERBS.items():
            if token in verbs:
                return operation

        # Short words are excluded outright. `fuzzy_ratio` scores a
        # three-letter prefix at 0.94, so "get" would match "getting"
        # harmlessly but "new" would match "news" - and the budget in
        # text.py already forgives no edits below five letters.
        if len(token) < 5:
            return None

        best_operation: Operation | None = None
        best_score = FUZZY_VERB_THRESHOLD

        for operation, verbs in INTENT_VERBS.items():
            for verb in verbs:

                if len(verb) < 5:
                    continue

                score = fuzzy_ratio(token, verb)

                if score > best_score:
                    best_score = score
                    best_operation = operation

        return best_operation

    @classmethod
    def detect_intent(cls, raw_tokens: list[str]) -> Operation | None:
        """
        What kind of action the user is asking for.

        The FIRST recognized verb wins. English imperatives lead with
        the action - "show me my open issues" starts with "show", and
        the later word "open" (a write verb) is a red herring. Scanning
        left to right handles that correctly and is easy to reason
        about, which matters more than cleverness for a safety signal.

        Typos are forgiven, at a stricter threshold than the rest of
        the router uses. Every other stage here has been typo-tolerant
        since Phase 2.3; this one was not, and a real request paid for
        it: "repo summury provide me" resolved to no intent, so the
        read-only filter below never ran and a delete tool was offered
        to a request that only wanted to look at something.
        """

        for token in raw_tokens:

            operation = cls._verb_operation(token)

            if operation is not None:
                return operation

        return None

    @classmethod
    def mentioned_operations(cls, raw_tokens: list[str]) -> frozenset[Operation]:
        """
        EVERY kind of action the query named, not just the first.

        WHY THE PRIMARY INTENT IS NOT ENOUGH

        `detect_intent` takes the first verb, which is right for an
        imperative and wrong the moment a sentence names two kinds of
        action:

            "list all github tools for write or update operations"
             ^^^^ intent = READ            ^^^^^  ^^^^^^ never seen

        The first verb decided the intent, `operation_alignment` then
        scored every write tool at a hard 0.0, and the eight-tool
        budget filled up with reads. The agent answered, truthfully,
        that it had no write tools - while holding four of them.

        A user who types the word "write" has told us writes are on
        topic. Keeping the full set lets alignment demote a tool the
        user did not ask for WITHOUT erasing one they named out loud.

        Note what this does NOT change: a query naming only read verbs
        returns only READ, so "show me my files" still scores
        delete_file at zero. The rule fires on what the user said, not
        on a guess about what they meant.
        """

        found = (
            cls._verb_operation(token)
            for token in raw_tokens
        )

        return frozenset(
            operation
            for operation in found
            if operation is not None
        )

    @classmethod
    def clause_intents(
        cls,
        normalized_tokens: list[str],
    ) -> list[tuple[Operation | None, frozenset[Operation], list[str]]]:
        """
        One intent per clause, instead of one intent per sentence.

        WHY A SENTENCE IS THE WRONG UNIT

        `detect_intent` takes the first verb and applies it to the
        whole message. That is right for an imperative and wrong the
        moment somebody asks for two things, which they constantly do:

            "list my repos and delete the stale branch"
             ^^^^ READ, applied to everything

        Every DELETE tool then scored 0.0 on alignment - the branch
        could not be deleted, because no tool that deletes anything was
        offered. `mentioned_operations` softened that (the user did
        type "delete", so it is demoted rather than erased), but
        softening a wrong answer is not the same as splitting the
        request in two.

        Splitting on connectives is crude and it is honest about being
        crude: it recovers "do X and then do Y", which is the shape
        people actually type. It does NOT recover a single clause that
        spans two services - "post a slack summary of my github
        issues" is one clause with one verb, and separating the
        posting from the reading needs a parser. That case is covered
        below the scoring, by MIN_READ_TOOLS_PER_NAMESPACE.

        Returns (intent, mentioned, tokens) per clause so the caller
        can map each one onto the services that clause named.
        """

        clauses: list[list[str]] = [[]]

        for token in normalized_tokens:

            if token in CLAUSE_SEPARATORS:
                # A separator with nothing before it is not a
                # separator, it is how the sentence started.
                if clauses[-1]:
                    clauses.append([])
                continue

            clauses[-1].append(token)

        return [
            (
                cls.detect_intent(clause),
                cls.mentioned_operations(clause),
                clause,
            )
            for clause in clauses
            if clause
        ]

    @staticmethod
    def _task_expansions(
        index: ToolIndex,
        content_tokens: list[str],
        already_present: set[str],
    ) -> list[TokenExpansion]:
        """
        Extra search terms implied by the TASK the user named.

        "Summarise this repo" contains no word that any tool is named
        after. The words that ARE - readme, file, content - are the
        ones a person would use to describe HOW you summarise a repo,
        which is knowledge about the service rather than about the
        sentence. TASK_EXPANSIONS holds it; this turns it into
        expansions the scorer already knows how to consume.

        Three properties worth noticing, because they are what make an
        inference table safe to keep adding to:

          IT CANNOT INVENT A TERM. Every expansion is looked up in the
          index vocabulary. A term no tool uses produces no expansion
          at all, so a stale entry rots into a no-op rather than into a
          wrong answer.

          IT CANNOT SHOUT OVER THE USER. Weight is scaled by
          TASK_EXPANSION_WEIGHT_SCALE, so an implied term always ranks
          below a typed one.

          IT CANNOT DOUBLE-COUNT. A term the user typed themselves is
          skipped - otherwise "summarise the readme" would score
          "readme" twice and quietly outrank "summarise the commits".

        Typos are resolved through the same fuzzy machinery as
        everything else, so "summury" still finds the entry. This is
        the reason the table is looked up here rather than in the
        lexicon: the thresholds live on this side.
        """

        matched_terms: dict[str, float] = {}

        for token in content_tokens:

            terms = expansions_for_task(token)

            if not terms:
                # Not an exact task word. Try it as a misspelling of
                # one - a single fuzzy pass over a table of ~20 keys.
                best_key = ""
                best_score = SCORE_TYPO_FLOOR

                for key in TASK_EXPANSIONS:

                    score = fuzzy_ratio(token, key)

                    if score > best_score:
                        best_score = score
                        best_key = key

                terms = expansions_for_task(best_key)

            for term in terms:

                if term in already_present or term in matched_terms:
                    continue

                # Only terms the tool corpus actually uses.
                weight = index.idf(term) if term in index.vocabulary else 0.0

                if weight > 0.0:
                    matched_terms[term] = weight

        return [
            TokenExpansion(
                token=term,
                weight=weight * TASK_EXPANSION_WEIGHT_SCALE,
                matches={term: 1.0},
            )
            for term, weight in matched_terms.items()
        ]

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
        mentioned: frozenset[Operation] = frozenset(),
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

        `mentioned` is the escape hatch, and it exists because the
        first rule above was firing on sentences it was never meant
        for. "List all github tools for WRITE or UPDATE operations"
        reads as READ intent, so every write tool scored 0.0 and the
        agent reported itself read-only while holding four write
        tools. A user who typed the word is not guessing, so a kind of
        action the query NAMED is never zeroed - only demoted, which
        is enough to keep the tool they actually asked for on top.
        """

        if intent is None:

            # No verb detected. Stay neutral rather than guess - but
            # neutrality is not symmetric. A tool that destroys
            # something has to be ASKED for; silence is not consent.
            #
            # The flat 0.5 that used to be returned here is how
            # github_delete_repository came to be offered alongside
            # seven other repository tools for the message "repo
            # summury provide me". Nothing in that sentence suggested
            # deleting anything; the router simply had no opinion, and
            # "no opinion" ranked the delete tool level with the rest.
            if operation in (Operation.DELETE, Operation.ADMIN):
                return UNASKED_DESTRUCTIVE_ALIGNMENT

            return 0.5

        if intent is operation:
            return 1.0

        # The user named this kind of action explicitly, even though it
        # was not the leading verb. Below an exact match, above every
        # mismatch - relevant, but never the thing that wins a tie.
        if operation in mentioned:
            return 0.7

        if intent is Operation.READ:
            return 0.0

        if operation is Operation.READ:
            # Reads are prerequisites for writes.
            return 0.6

        # Both mutate, but not in the same way (e.g. asked to update,
        # tool deletes). Plausible, but not what was asked for.
        return 0.35

    @staticmethod
    def lookup_stems(tool: ToolDefinition) -> set[str]:
        """
        The things this tool needs an ID for.

            slack_send_message(channel_id, text) -> {"channel"}
            github_create_issue(owner, repo, ...) -> set()

        WHY IDs SPECIFICALLY

        An id is the one kind of argument a person never types. Someone
        writes "post it to #mcp_test", never "post it to C09FT2X5A".
        So a tool with a required `*_id` argument is nearly always a
        two-step job: look the name up, then act.

        `operation_alignment` already keeps read tools alive for write
        requests for exactly this reason - but "a read tool" is not
        enough, it has to be the RIGHT one. A turn asking to post to a
        Slack channel was handed slack_list_users, slack_get_user and
        slack_auth_info: three reads, none of which can turn
        "#mcp_test" into a channel id.

        The stem is what makes the pairing findable: "channel" matches
        slack_list_channels' own name terms, with no table to maintain
        and nothing to update when a service gains a tool.
        """

        schema = tool.input_schema

        if not isinstance(schema, dict):
            return set()

        required = schema.get("required")

        if not isinstance(required, list):
            return set()

        stems: set[str] = set()

        for name in required:

            if not isinstance(name, str):
                continue

            lowered = name.lower()

            if not lowered.endswith(("_id", "_ids")):
                continue

            stem = lowered.split("_id")[0]

            # "id" alone says nothing about WHAT to look up, and a
            # one- or two-letter stem would match half the index.
            if len(stem) < 3:
                continue

            stems.add(normalize_token(stem))

        return stems

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

            parts = name.lower().split("_")

            # An argument that comes from another tool's OUTPUT is not
            # missing information, it is the second half of a plan.
            # Counting it as unfilled penalised every chainable tool -
            # see CHAINABLE_ARGUMENT_PARTS for the failure this caused.
            if any(part in CHAINABLE_ARGUMENT_PARTS for part in parts):
                continue

            hinted = any(
                fuzzy_ratio(part, term) > 0.0
                for part in parts
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

        # EVERY call to the provider is guarded, here at the router's
        # own boundary - not only inside the provider that ships with
        # this project.
        #
        # `EmbeddingProvider` is a Protocol: any object with `embed`
        # and `is_enabled` qualifies, and the whole point of that seam
        # is that somebody plugs in OpenAI, or torch, or an internal
        # service, without touching this file. Nothing in a Protocol
        # can promise not to raise.
        #
        # Trusting one implementation's internal try/except made an
        # OPTIONAL ranking signal able to kill a conversation: the
        # exception propagates out of `route`, out of `send_message`,
        # and the user sees a failed turn because a nice-to-have
        # ranking hint was unavailable. Degraded routing is the correct
        # outcome, and it has to be enforced by the caller.
        try:

            if missing:

                vectors = self._embeddings.embed([
                    f"{tool.name}. {tool.description or ''}"
                    for tool in missing
                ])

                for tool, vector in zip(missing, vectors):
                    self._tool_vectors[tool.name] = vector

            query_vectors = self._embeddings.embed([query])

        except Exception:
            return {}

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
    def _active_weights(
        semantic_enabled: bool,
        lexical_inconclusive: bool = False,
    ) -> dict[str, float]:
        """
        Weights for the signals actually in play, normalized to sum 1.

        Without renormalization, turning the semantic layer off would
        cap every score at 0.90 and silently shift the meaning of every
        threshold in this file. Normalizing keeps scores comparable
        whether or not embeddings are configured.

        `lexical_inconclusive` says the keyword layer did not decide:
        either nothing matched well, or everything matched equally.
        When that happens the weights are not describing reality any
        more - a 0.55 weight on a signal that is identical across the
        field contributes 0.55 x constant to everyone and decides
        nothing - so semantics take over for that query.

        Per query, not per deployment. Most requests name a tool's own
        vocabulary and lexical scoring is both faster and more precise;
        this promotes the black box only where the transparent signal
        has visibly failed.
        """

        weights = {
            "lexical": WEIGHT_LEXICAL,
            "namespace": WEIGHT_NAMESPACE,
            "operation": WEIGHT_OPERATION,
            "executability": WEIGHT_EXECUTABILITY,
        }

        if semantic_enabled:
            weights["semantic"] = (
                WEIGHT_SEMANTIC * SEMANTIC_FLAT_BOOST
                if lexical_inconclusive
                else WEIGHT_SEMANTIC
            )

        total = sum(weights.values())

        return {
            key: value / total
            for key, value in weights.items()
        }

    @staticmethod
    def _separation(scores: list[float]) -> float:
        """
        How far the best score stands above the middle of the field,
        as a fraction of the best. 0.0 means a tie.

        The median, not the runner-up, for the reason given at
        SEPARATION_TARGET: two strong candidates at the top is a normal
        healthy result, and penalising it would make the router
        distrust its own best days.
        """

        if len(scores) < 2:
            # One candidate cannot be ambiguous with anything.
            return 1.0

        ordered = sorted(scores, reverse=True)

        best = ordered[0]

        if best <= 0.0:
            return 0.0

        median = ordered[len(ordered) // 2]

        return max(0.0, (best - median) / best)

    @staticmethod
    def _lexical_inconclusive(scores: list[float]) -> bool:
        """
        Did keyword scoring find anything it could actually rank on?

        True when the best match in the whole pool is weak, which means
        the user described a GOAL rather than a tool - "what is this
        project built with" contains no word any tool is named after.
        That is the case semantics exist for, and the only case worth
        paying a network round trip on.

        See LEXICAL_DECISIVE for why this is a threshold on the best
        score rather than the tie test it looks like it should be.
        """

        return not scores or max(scores) < LEXICAL_DECISIVE

    # -----------------------------------------------------------------
    # Selection
    # -----------------------------------------------------------------

    @staticmethod
    def _select_fairly(
        ranked: list[ToolCandidate],
        selected_namespaces: list[str],
        top_k: int,
        reserve_operations: frozenset[Operation] = frozenset(),
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

        def reserve(quota: int, matches) -> None:
            """
            Take up to `quota` best-scoring unclaimed matching tools.

            NEVER exceeds top_k. The previous version let the reserve
            passes overfill and then trimmed the result by score, which
            silently threw away the very picks the passes existed to
            protect - always the lowest-scoring ones, which are exactly
            the tools a service was being guaranteed.
            """

            for candidate in ranked:

                if quota <= 0 or len(selected) >= top_k:
                    return

                if candidate.tool_name in taken:
                    continue

                if not matches(candidate):
                    continue

                selected.append(candidate)
                taken.add(candidate.tool_name)
                quota -= 1

        # Pass 1: every selected service gets a working set.
        #
        # Best service first, so a tight budget squeezes the weakest
        # one rather than the one the user most clearly named.
        for namespace in selected_namespaces:
            reserve(
                MIN_TOOLS_PER_NAMESPACE,
                lambda candidate, ns=namespace: candidate.namespace == ns,
            )

        # Pass 1b: and at least one way to LOOK at each service.
        #
        # Pass 1 fills its quota by score, and on a write-shaped
        # request every one of those slots can be a write tool. That is
        # how "post a slack summary of my github issues" ended up
        # offering GitHub three ways to modify issues and no way to
        # read one - the summary had nothing to summarise.
        #
        # Reads are the universal prerequisite: you look something up,
        # then you act on it. A service with no read tool offered can
        # only be written to blind.
        #
        # Runs after pass 1 rather than instead of it, and checks what
        # pass 1 already took: on a read-shaped request this pass is a
        # no-op, and costs nothing.
        for namespace in selected_namespaces:

            covered = sum(
                1
                for candidate in selected
                if candidate.namespace == namespace
                and candidate.tool is not None
                and candidate.tool.read_only
            )

            if covered >= MIN_READ_TOOLS_PER_NAMESPACE:
                continue

            reserve(
                MIN_READ_TOOLS_PER_NAMESPACE - covered,
                lambda candidate, ns=namespace: (
                    candidate.namespace == ns
                    and candidate.tool is not None
                    and candidate.tool.read_only
                ),
            )

        # Pass 2: and a way to DO each kind of action they asked for,
        # in each service they asked about.
        #
        # PER SERVICE, not globally, and that distinction is the whole
        # fix. A global write quota is won by whichever service scores
        # best, so:
        #
        #     "create 3 issues in the repo, then post a summary to
        #      #mcp_test on Slack"
        #
        # spent both write slots on GitHub. Slack got only its pass-1
        # quota, which by score is three read tools - so the agent was
        # handed slack_list_users, slack_get_user and slack_auth_info,
        # and correctly reported that it could not post anything. The
        # second half of the task was impossible before the model saw a
        # single token.
        #
        # A quota per (service, action) makes a multi-service, multi-
        # step request routable: one guaranteed way to write on GitHub,
        # one guaranteed way to write on Slack.
        for namespace in selected_namespaces:
            for operation in sorted(
                reserve_operations,
                key=lambda op: op.value,
            ):
                reserve(
                    MIN_TOOLS_PER_OPERATION,
                    lambda candidate, ns=namespace, op=operation: (
                        candidate.namespace == ns
                        and candidate.tool is not None
                        and candidate.tool.operation is op
                    ),
                )

        # Pass 2b: the LOOKUP each selected tool needs to be callable.
        #
        # Reserving a way to write is not enough if the write cannot be
        # addressed. slack_send_message takes a channel_id, and nobody
        # types a channel id - so a turn ending "post a summary to
        # #mcp_test on Slack" needs slack_list_channels to turn the
        # name into an id.
        #
        # It did not get it. Slack's three guaranteed slots went to its
        # best-scoring reads - list_users, get_user, auth_info - none of
        # which can resolve a channel, and the model was left holding a
        # write tool it had no way to call.
        #
        # Paired by name rather than by a table: the stem "channel"
        # matches slack_list_channels' own indexed terms, so a service
        # that gains a tool tomorrow is paired without anyone
        # remembering to add it here.
        for candidate in list(selected):

            if candidate.tool is None:
                continue

            for stem in sorted(ToolRouter.lookup_stems(candidate.tool)):
                reserve(
                    1,
                    lambda other, ns=candidate.namespace, st=stem: (
                        other.namespace == ns
                        and other.tool is not None
                        and other.tool.read_only
                        and st in other.tool.name_terms
                    ),
                )

        # Pass 3: fill what remains by pure score.
        reserve(top_k, lambda candidate: True)

        # Sorted for presentation only. Nothing is dropped here - the
        # reserve passes already respected the budget.
        selected.sort(key=lambda item: item.score, reverse=True)

        return selected

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
        allow: Callable[[ToolDefinition], bool] | None = None,
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
        back to all 161 tools. Given the previous context they route
        correctly. This is the routing half of the same problem your
        system prompt already addresses for the LLM.

        `exclude` removes tools from consideration entirely. This is
        what makes a SECOND routing pass useful: when the first set of
        tools turns out not to work, the agent can ask for a different
        set rather than being handed the same failures again. See
        `agent/loop.py` for the escalation that uses it.

        `allow` drops tools this CALLER cannot use before any budget is
        spent on them. The backend passes its permission policy, and
        the reason is arithmetic: the caller used to filter the result
        AFTERWARDS, so a service the agent holds no scope for could win
        pooled slots, take its guaranteed per-service quota, and have
        every one of them thrown away - leaving the model with fewer
        tools than the budget allowed.

        A real turn: "create 3 issues in the repo, then post a summary
        to Slack" pooled Google Drive on the word "file", spent 4 of 16
        slots there, and the model received 12. Filtering here spends
        the whole budget on tools that can actually run.

        Routing, not authorisation. The executor's gate is unchanged
        and still runs on whatever the model asks for.
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
        normalized_tokens = [
            normalize_token(token)
            for token in raw_tokens
        ]

        intent = self.detect_intent(normalized_tokens)

        # Every kind of action the query named, not only the leading
        # verb. Used below so a tool the user asked for by name is
        # demoted rather than erased. See mentioned_operations().
        mentioned = self.mentioned_operations(normalized_tokens)

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

        # --- Which action, for which service? ------------------------
        #
        # "list my repos and delete the stale branch" asks for two
        # different kinds of action. One sentence-level intent has to
        # pick one of them and be wrong about the other; a map from
        # service to intent can be right about both.
        #
        # Only clauses that name a service contribute. A clause with no
        # service ("and then tell me what changed") leaves the global
        # intent in place, which is the correct reading of a follow-on
        # that does not switch topic.
        namespace_intents: dict[str, Operation] = {}
        namespace_mentioned: dict[str, frozenset[Operation]] = {}

        for clause_intent, clause_mentioned, clause_tokens in (
            self.clause_intents(normalized_tokens)
        ):

            if clause_intent is None:
                continue

            clause_content = [
                token
                for token in clause_tokens
                if token not in STOPWORDS
            ]

            for entry in self.score_namespaces(
                clause_tokens,
                clause_content,
            ):
                # First clause to claim a service wins. Re-reading it
                # later in the sentence ("...and check github again")
                # is a continuation, not a correction.
                namespace_intents.setdefault(
                    entry.namespace,
                    clause_intent,
                )

                namespace_mentioned.setdefault(
                    entry.namespace,
                    clause_mentioned,
                )

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
        #
        # Service names get the same treatment for the same reason:
        # they are already counted, in full, by the namespace prior.
        # Both scales are applied to the same expansion, so a token
        # that is both (there are none today, but "search" and "share"
        # are one lexicon edit away) is not silently exempted from one
        # of them.
        # Both corrections scale the same token weight. A service
        # name is suppressed because the namespace prior already
        # counts it; a command verb because intent detection already
        # consumed it. Applied together, so a token that is both is
        # not silently exempted from one of them.
        service_terms = index.service_terms

        expansions = [
            replace(expansion, weight=expansion.weight * scale)
            if (
                scale := (
                    (
                        INTENT_VERB_WEIGHT_SCALE
                        if expansion.token in ALL_INTENT_VERBS
                        else 1.0
                    )
                    * (
                        SERVICE_TERM_WEIGHT_SCALE
                        if expansion.token in service_terms
                        else 1.0
                    )
                )
            ) != 1.0
            else expansion
            for expansion in expansions
        ]

        # Report only tokens that SHOULD have matched. A verb like
        # "show" is consumed by intent detection and is not expected to
        # appear in any tool's vocabulary, so listing it here would
        # bury the signal we actually want: real nouns the lexicon does
        # not know yet.
        #
        # Computed BEFORE the task expansion below, deliberately.
        # Synthetic terms are not words the user typed, and reporting
        # them here would make this diagnostic - the one that tells you
        # which real nouns your lexicon is missing - noisier for no
        # gain.
        unmatched = tuple(
            expansion.token
            for expansion in expansions
            if expansion.is_out_of_vocabulary
            and expansion.token not in ALL_INTENT_VERBS
        )

        # A task word names a PLAN, and a plan is carried out with
        # tools whose names do not contain it. Add the terms that plan
        # is made of, at reduced weight. See TASK_EXPANSIONS.
        expansions = expansions + self._task_expansions(
            index,
            content_tokens,
            already_present={
                expansion.token
                for expansion in expansions
            },
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
            and (allow is None or allow(tool))
        ]

        if not pool:
            return self._fallback(
                query=query,
                intent=intent,
                mentioned=mentioned,
                allow=allow,
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
        #
        # Unless the user NAMED that kind of action. "which files can I
        # delete" is a read-shaped sentence about deletion, and hiding
        # every delete tool from it makes the agent deny it has any.
        # Nothing is loosened by keeping them visible: routing only
        # decides what to OFFER, and the scope gate plus the approval
        # prompt both still stand between the model and the call.
        if intent is Operation.READ:
            pool = [
                tool
                for tool in pool
                if tool.operation
                not in (Operation.DELETE, Operation.ADMIN)
                or tool.operation in mentioned
            ]

        # Every word the user actually typed, for the executability
        # check. Built once, not per tool.
        query_terms = set(content_tokens)

        # --- Lexical first, on its own -------------------------------
        #
        # Scored in a separate pass so the weights can be chosen with
        # knowledge of how the field came out. A weight set fixed
        # before scoring cannot notice that its heaviest signal
        # returned the same number for every candidate - which is
        # exactly what happened on "repo summury": eleven tools tied at
        # 0.188, and lexical still carried 55% of the decision.
        lexical_scores = {
            tool.name: index.lexical_score(tool, expansions)
            for tool in pool
        }

        lexical_inconclusive = self._lexical_inconclusive(
            list(lexical_scores.values())
        )

        # --- Semantics, only when keywords could not decide ----------
        #
        # A RESCUE SIGNAL, NOT A TAX ON EVERY MESSAGE.
        #
        # Embedding the query is one network round trip - about 150ms
        # here, measured, against 15ms for the rest of routing. Paid on
        # every message it would be the most expensive thing the router
        # does, and it would be paid mostly on requests that did not
        # need it: "list my github issues" is ranked correctly and
        # decisively by keywords alone, and a semantic score worth 0.18
        # will not reorder a field that is already separated.
        #
        # It is worth every millisecond on the queries that ARE tied,
        # because there the keyword layer is contributing nothing and
        # the ranking is noise. So it runs exactly there.
        #
        # Worth being explicit about the shape of this, because it
        # generalises: the expensive signal is not "better" than the
        # cheap one, it is better AT SOMETHING ELSE. Run the cheap one
        # first, measure whether it worked, and spend the money only
        # when it did not.
        semantic = (
            self._semantic_scores(query, pool)
            if lexical_inconclusive
            else {}
        )

        weights = self._active_weights(
            bool(semantic),
            lexical_inconclusive=lexical_inconclusive,
        )

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

            lexical = lexical_scores[tool.name]

            namespace_prior = namespace_lookup.get(
                tool.namespace or "",
                0.0,
            )

            # The intent for THIS tool's service, when the request
            # said something specific about it. "list my repos and
            # delete the stale branch" means READ for one service and
            # DELETE for another; a single sentence-level intent has
            # to be wrong about one of them.
            tool_namespace = tool.namespace or ""

            alignment = self.operation_alignment(
                namespace_intents.get(tool_namespace, intent),
                tool.operation,
                namespace_mentioned.get(tool_namespace, mentioned),
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

            effective_intent = namespace_intents.get(
                tool_namespace,
                intent,
            )

            if effective_intent is not None and alignment >= 1.0:
                reasons.append(f"intent {effective_intent.value}")

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

        # Kept before the floor trims it, for the fallback below.
        #
        # When the router decides it is not confident, the ranking it
        # just produced is weak evidence - but weak evidence beats the
        # none it used to fall back to. See `_fallback`.
        ranked_full = list(ranked)

        # Relative floor: keep only what is competitive with the best.
        best_score = ranked[0].score if ranked else 0.0
        floor = best_score * RELATIVE_SCORE_FLOOR

        ranked = [
            candidate
            for candidate in ranked
            if candidate.score >= floor
        ]

        # Any kind of action the user named that is NOT the leading
        # verb gets reserved slots. When the two agree there is nothing
        # to protect - those tools already win on alignment.
        selected = self._select_fairly(
            ranked,
            pooled,
            top_k,
            frozenset(mentioned) - {intent},
        )

        # --- How sure are we? ----------------------------------------

        namespace_confidence = (
            namespace_scores[0].score
            if namespace_scores
            else 0.0
        )

        tool_confidence = selected[0].score if selected else 0.0

        # How far clear of the field did the winner finish?
        #
        # Without this term, confidence answers "did anything score
        # well?" when the question that matters is "did anything WIN?".
        # A field bunched inside 0.06 is a coin toss dressed up as a
        # decision, and reporting it as 0.70 confident is what kept the
        # fallback from firing on the query that motivated it.
        separation = self._separation(
            [candidate.score for candidate in selected]
        )

        confidence = (
            0.4 * namespace_confidence
            + 0.6 * tool_confidence
        ) * (
            SEPARATION_FLOOR
            + (1.0 - SEPARATION_FLOOR)
            * min(1.0, separation / SEPARATION_TARGET)
        )

        if confidence < FALLBACK_CONFIDENCE or not selected:
            return self._fallback(
                query=query,
                intent=intent,
                mentioned=mentioned,
                allow=allow,
                namespace_scores=namespace_scores,
                unmatched=unmatched,
                top_k=top_k,
                started=started,
                ranked=ranked_full,
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
        mentioned: frozenset[Operation] = frozenset(),
        allow: Callable[[ToolDefinition], bool] | None = None,
        ranked: list[ToolCandidate] | None = None,
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

        WIDEN THE BEAM, DO NOT THROW AWAY THE RANKING

        `ranked` is what the scoring pass produced before confidence
        was judged too low to trust. Low confidence means "I am not
        sure which of these wins", NOT "these scores are meaningless" -
        and the difference decides what the model gets:

            without ranked   the first 20 tools in INDEX ORDER, which
                             is discovery order, which is the order
                             they happen to appear in tools.py
            with ranked      the 20 best-scoring tools

        On the repo-summary query the index-order fallback offered
        `github_get_file` (by luck, it is declared early) and left out
        `github_get_readme` (declared later) - dropping the single best
        tool for the request from the very set that exists to stop that
        happening. Uncertainty is a reason to take MORE of the ranking,
        never a reason to stop using it.
        """

        index = self.index

        # Widened, but still bounded. Read-only requests fan out more
        # because they are safe.
        limit = max(top_k * 2, 20)

        # The ranking first, when there is one.
        candidates: list[ToolCandidate] = [
            replace(
                candidate,
                reasons=candidate.reasons
                + ("fallback: low routing confidence",),
            )
            for candidate in (ranked or ())[:limit]
        ]

        taken = {candidate.tool_name for candidate in candidates}

        tools = [
            tool
            for tool in index.tools
            if (allow is None or allow(tool))
            and tool.name not in taken
        ]

        # Same exception as the main path: a kind of action the query
        # NAMED stays visible even when the leading verb reads as a
        # read. Widening on uncertainty is this function's whole point,
        # and silently dropping the tools the user asked about is the
        # one narrowing it must not do.
        if intent is Operation.READ:
            tools = [
                tool
                for tool in tools
                if tool.read_only or tool.operation in mentioned
            ]

        # TOPPED UP, not replaced.
        #
        # The ranking can be both trustworthy and TINY. When the query
        # names no service, the candidate pool is only the tools with a
        # direct term match - so a message like "try again and send me
        # current data, not old" ranked four tools, two of which were
        # `delete_event` and `rename_channel`, and returning just those
        # would be a narrower answer than the router gives when it is
        # confident. Uncertainty must never produce FEWER options than
        # certainty.
        #
        # Ranked candidates first because they are ordered by evidence;
        # the rest follow to fill the budget.
        candidates.extend(
            ToolCandidate(
                tool_name=tool.name,
                namespace=tool.namespace,
                score=0.0,
                reasons=("fallback: low routing confidence",),
                tool=tool,
            )
            for tool in tools[: max(0, limit - len(candidates))]
        )

        return RoutingDecision(
            candidates=tuple(candidates),
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
