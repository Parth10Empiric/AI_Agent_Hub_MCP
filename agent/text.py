from __future__ import annotations

import unicodedata
from functools import lru_cache

"""
Low-level text primitives used by the Agent Engine.

Everything in this module is a **pure function**: same input always
produces the same output, no I/O, no global state. That is deliberate.

Why pure functions matter here:

- They are trivial to unit test (no MCP server, no LLM, no network).
- They can be cached safely (see `fuzzy_ratio`).
- Routing bugs become reproducible: if a query mis-routes, you can
  replay the exact token comparison in a REPL.

This module knows NOTHING about tools, MCP, or namespaces. It only
knows about strings. Keeping it that way is what lets the router stay
small and readable.
"""


# Cache size for fuzzy comparisons.
#
# A single user query compares ~10 tokens against ~500 indexed terms,
# so the same (token, term) pairs repeat constantly across requests.
_FUZZY_CACHE_SIZE = 8192


# ---------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------


def strip_accents(value: str) -> str:
    """
    Remove diacritics so "reunión" matches "reunion".

    NFKD splits an accented character into (base char + combining mark),
    then we drop the combining marks.
    """

    decomposed = unicodedata.normalize("NFKD", value)

    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char)
    )


def normalize_text(value: str | None) -> str:
    """
    Convert free text into a clean, lowercase, space-separated form.

    Every non-alphanumeric character becomes a space. This is what turns
    "github_list_repositories" into "github list repositories" and
    "What's on my calendar?" into "what s on my calendar".

    NOTE: this intentionally does NOT split camelCase. Descriptions
    contain brand words like "GitHub", and splitting them into
    "git hub" would destroy the strongest routing signal we have.
    Identifier splitting is handled separately by `split_identifier`.
    """

    if not value:
        return ""

    lowered = strip_accents(value).lower()

    characters = [
        char if char.isalnum() else " "
        for char in lowered
    ]

    return " ".join("".join(characters).split())


def split_identifier(identifier: str | None) -> list[str]:
    """
    Split a code identifier into words.

    Handles both conventions an MCP server might use:

        github_list_repositories  -> [github, list, repositories]
        githubListRepositories    -> [github, list, repositories]

    Our own server uses snake_case, but a third-party MCP server
    (Phase "custom MCP servers") may not. Handling both now costs
    five lines and avoids a migration later.
    """

    if not identifier:
        return []

    if "_" in identifier or "-" in identifier:
        return normalize_text(identifier).split()

    # No separators: assume camelCase and insert boundaries.
    characters: list[str] = []

    for index, char in enumerate(identifier):

        is_boundary = (
            index > 0
            and char.isupper()
            and (
                identifier[index - 1].islower()
                or identifier[index - 1].isdigit()
            )
        )

        if is_boundary:
            characters.append(" ")

        characters.append(char)

    return normalize_text("".join(characters)).split()


def tokenize(value: str | None) -> list[str]:
    """
    Normalize free text and split it into tokens.
    """

    return normalize_text(value).split()


# ---------------------------------------------------------------------
# Singularization
# ---------------------------------------------------------------------


_IRREGULAR_PLURALS = {
    "people": "person",
    "children": "child",
    "men": "man",
    "women": "woman",
}

# Words that end in "s" but are NOT plural. Stripping the "s" here
# would corrupt the token ("status" -> "statu").
_PROTECTED_SUFFIXES = ("ss", "us", "is", "os")


def singularize(token: str) -> str:
    """
    Reduce a plural token to its singular form.

    This exists so the user's word and the tool's word meet in the
    middle. The user types "issues"; the tool is named
    "github_list_issues" but the useful concept is "issue".

    This is a deliberately *crude* stemmer, not a linguistic one. A
    real stemmer (Porter/Snowball) would also turn "repositories" into
    "repositori", which reads as noise in debug output. For routing we
    only need the common English plural cases.
    """

    if len(token) <= 3:
        return token

    if token in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[token]

    if token.endswith("ies") and len(token) > 4:
        # repositories -> repository
        return token[:-3] + "y"

    if token.endswith(("ches", "shes", "ses", "xes", "zes")):
        # branches -> branch
        return token[:-2]

    if token.endswith("s") and not token.endswith(_PROTECTED_SUFFIXES):
        # commits -> commit
        return token[:-1]

    return token


def normalize_token(token: str) -> str:
    """
    The canonical form of a token used everywhere in the index.
    """

    return singularize(token)


# ---------------------------------------------------------------------
# Edit distance
# ---------------------------------------------------------------------


def damerau_levenshtein(
    first: str,
    second: str,
    max_distance: int | None = None,
) -> int:
    """
    Optimal String Alignment distance between two strings.

    This counts the minimum number of single-character operations
    needed to turn `first` into `second`:

        insertion       "githb"  -> "github"     (1)
        deletion        "githubb"-> "github"     (1)
        substitution    "calender" -> "calendar" (1)
        transposition   "githbu" -> "github"     (1)   <-- the important one

    Why Damerau and not plain Levenshtein?

    Transposition (two adjacent characters swapped) is the single most
    common human typo. Plain Levenshtein scores "githbu" -> "github"
    as distance 2 (one delete + one insert), which pushes a very
    ordinary typo outside any sane threshold. Damerau scores it as 1.
    That one extra branch in the loop is the difference between a
    router that tolerates real typing and one that does not.

    `max_distance` enables early exit. Once every value in the current
    DP row exceeds the budget, the final distance cannot come back
    under it, so we stop and return `max_distance + 1` as a sentinel
    meaning "further than you care about". This turns the worst case
    from O(n*m) into roughly O(n*max_distance).
    """

    if first == second:
        return 0

    length_first = len(first)
    length_second = len(second)

    if length_first == 0:
        return length_second

    if length_second == 0:
        return length_first

    # A length gap alone already exceeds the budget.
    if (
        max_distance is not None
        and abs(length_first - length_second) > max_distance
    ):
        return max_distance + 1

    # Classic dynamic programming, but we only keep three rows instead
    # of the full matrix: the transposition rule needs to look two rows
    # back, and nothing needs to look further than that.
    two_rows_back: list[int] = []
    previous_row = list(range(length_second + 1))

    for i in range(1, length_first + 1):

        current_row = [i] + [0] * length_second
        char_first = first[i - 1]

        for j in range(1, length_second + 1):

            cost = 0 if char_first == second[j - 1] else 1

            current_row[j] = min(
                previous_row[j] + 1,          # deletion
                current_row[j - 1] + 1,       # insertion
                previous_row[j - 1] + cost,   # substitution
            )

            is_transposition = (
                i > 1
                and j > 1
                and char_first == second[j - 2]
                and first[i - 2] == second[j - 1]
            )

            if is_transposition:
                current_row[j] = min(
                    current_row[j],
                    two_rows_back[j - 2] + cost,
                )

        if (
            max_distance is not None
            and min(current_row) > max_distance
        ):
            return max_distance + 1

        two_rows_back = previous_row
        previous_row = current_row

    return previous_row[length_second]


def edit_budget(length: int) -> int:
    """
    How many typos we forgive in a word of this length.

    A fixed threshold is wrong in both directions: allowing 2 edits on
    a 3-letter word makes "cat" match "car" and "cap"; allowing only 1
    edit on a 14-letter word rejects "repositoriess".

    Scaling the budget with length keeps precision on short words and
    recall on long ones.
    """

    if length <= 4:
        # Four letters is still too short: "chat"/"what" and
        # "meet"/"meat" are one edit apart but unrelated. Real typos in
        # short words are better caught by the prefix rule above.
        return 0

    if length <= 6:
        return 1

    if length <= 11:
        return 2

    return 3


# ---------------------------------------------------------------------
# Fuzzy similarity
# ---------------------------------------------------------------------


# Scores returned for each kind of match. Named so that debug output
# and tests can assert on intent rather than on magic numbers.
SCORE_EXACT = 1.0
SCORE_PREFIX = 0.94
SCORE_CONTAINED = 0.90
SCORE_TYPO_FLOOR = 0.80

# Minimum length before a word may match as a PREFIX ("doc" ->
# "document"). A shared prefix is strong evidence even when short.
MIN_PREFIX_LENGTH = 3

# Minimum length before a word may match by EDIT DISTANCE. Higher than
# the prefix floor because an edit is weak evidence in a short word:
# "cat"/"car" are one edit apart and unrelated.
MIN_FUZZY_LENGTH = 4


@lru_cache(maxsize=_FUZZY_CACHE_SIZE)
def fuzzy_ratio(first: str, second: str) -> float:
    """
    Similarity between two single words, in the range 0.0 - 1.0.

    The rules are applied in order, strongest first:

      1. Exact match                    "issue"  == "issue"       1.00
      2. Prefix / abbreviation          "repo"   -> "repository"  0.94
      3. Containment                    "commit" in "precommit"   0.90
      4. Typo within the edit budget    "githb"  -> "github"      0.80+
      5. Otherwise                                                0.00

    Returning a hard 0.0 for anything outside the budget is a design
    choice. A graded "0.4-ish" similarity for unrelated words sounds
    more sophisticated, but in practice it just adds noise that has to
    be filtered out again by a threshold. Here, any non-zero score
    already means "this is plausibly the same word", so downstream
    code can trust it.
    """

    if not first or not second:
        return 0.0

    if first == second:
        return SCORE_EXACT

    shorter, longer = (
        (first, second)
        if len(first) <= len(second)
        else (second, first)
    )

    if len(shorter) < MIN_PREFIX_LENGTH:
        # Two letters. "pr", "gh" and "dm" are real aliases but must
        # match exactly, or they collide with half the dictionary.
        return 0.0

    if longer.startswith(shorter):
        return SCORE_PREFIX

    if len(shorter) < MIN_FUZZY_LENGTH:
        return 0.0

    if len(shorter) >= 5 and shorter in longer:
        return SCORE_CONTAINED

    budget = edit_budget(len(longer))

    if budget == 0:
        return 0.0

    distance = damerau_levenshtein(first, second, budget)

    if distance <= budget:
        # Graded inside the budget: one typo in a long word is a
        # stronger match than one typo in a short word.
        return max(
            SCORE_TYPO_FLOOR,
            1.0 - (distance / len(longer)),
        )

    return 0.0


def best_match(token: str, candidates: object) -> float:
    """
    Highest `fuzzy_ratio` between one token and any candidate term.

    Short-circuits on an exact match, which is the common case and
    lets us skip the remaining comparisons entirely.
    """

    best = 0.0

    for candidate in candidates:  # type: ignore[attr-defined]

        score = fuzzy_ratio(token, candidate)

        if score >= SCORE_EXACT:
            return SCORE_EXACT

        if score > best:
            best = score

    return best
