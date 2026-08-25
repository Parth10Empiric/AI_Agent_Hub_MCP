from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.errors import ErrorCode, ToolError  # noqa: E402
from services.github.services import GitHubService  # noqa: E402

"""
Three failures from one real agent turn, and why each one was fatal.

    github_get_file(path="/")
        -> "'list' object has no attribute 'get'"
        Retried three times, 3.7s, then gave up.

    github_search_issues(query="repo:Parth10Empiric/Empira_HR")
        -> 422 "Query must include 'is:issue' or 'is:pull-request'"

    ...and the model was shown neither reason, so it could not fix
    either call. It concluded the repository was "too minimal" to have
    issues worth filing. The repository is a Django project with 15
    entries at its root; the agent had simply never been able to look.

No network here. The GitHub shapes are the ones the API really returns
- verified against the live endpoint - so these hold without a token.
"""


# ---------------------------------------------------------------------
# The search qualifier
# ---------------------------------------------------------------------


def test_a_bare_repo_query_gets_the_required_qualifier():
    # This exact query returned 422. The endpoint cannot run without
    # one of the two qualifiers, so the tool has to supply one.
    got = GitHubService._qualified_issue_query(
        "repo:Parth10Empiric/Empira_HR"
    )

    assert got == "repo:Parth10Empiric/Empira_HR is:issue"


def test_an_explicit_qualifier_is_left_alone():
    for query in (
        "repo:o/n is:issue",
        "repo:o/n is:pull-request",
        "repo:o/n IS:ISSUE",
    ):
        assert GitHubService._qualified_issue_query(query) == query


def test_asking_for_pull_requests_is_not_overridden():
    # The default must never turn a PR search into an issue search.
    got = GitHubService._qualified_issue_query("repo:o/n is:pull-request")

    assert "is:issue" not in got


# ---------------------------------------------------------------------
# Files versus directories
# ---------------------------------------------------------------------


class FakeGitHub:
    """Returns the two shapes the contents endpoint really returns."""

    DIRECTORY = [
        {"name": "README.md", "path": "README.md", "type": "file", "size": 24},
        {"name": "manage.py", "path": "manage.py", "type": "file", "size": 687},
        {"name": "core", "path": "core", "type": "dir", "size": 0},
    ]

    FILE = {
        "name": "README.md",
        "path": "README.md",
        "type": "file",
        "size": 24,
        "sha": "abc123",
        "encoding": "base64",
        "content": "RW1waXJhX0hS",
    }

    def __init__(self) -> None:
        self.paths: list[str] = []

    async def get_contents(self, owner, repo, path="", ref=None):
        self.paths.append(path)

        return self.FILE if path.strip("/") == "README.md" else self.DIRECTORY


class Recorder:
    """Stands in for MCPServer, keeping every registered function."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


def call_get_file(**kwargs) -> dict:
    """
    Run the REAL github_get_file against a fake service.

    The module-level `github` name has to stay swapped for the whole
    call, not just for registration - the tool body resolves it at call
    time, which is the entire point of the ServiceProxy indirection.
    Restoring it too early meant the tool talked to the real proxy and
    the @github_tool decorator turned the resulting failure into an
    error dict, which is what the first version of this test measured.
    """

    import services.github.tools as tools_module

    recorder = Recorder()

    original = tools_module.github
    tools_module.github = FakeGitHub()

    try:
        tools_module.register_github_tools(recorder)

        return asyncio.run(recorder.tools["github_get_file"](**kwargs))

    finally:
        tools_module.github = original


def test_the_repository_root_lists_instead_of_crashing():
    """
    The crash. `.get()` on a list, dressed up as a service_error - and
    service_error on a read is retryable, so it happened three times.
    """

    result = call_get_file(owner="o", repo="r", path="/")

    assert result["type"] == "dir"
    assert result["count"] == 3
    assert [e["name"] for e in result["entries"]] == [
        "README.md",
        "manage.py",
        "core",
    ]


def test_an_empty_path_also_means_the_root():
    # There was no way at all to ask "what is in this repository?",
    # which is why the model improvised "/" in the first place.
    result = call_get_file(owner="o", repo="r")

    assert result["type"] == "dir"


def test_a_file_still_returns_its_content():
    result = call_get_file(owner="o", repo="r", path="README.md")

    assert result["type"] == "file"
    assert result["content"] == "RW1waXJhX0hS"
    assert result["encoding"] == "base64"


# ---------------------------------------------------------------------
# The reason neither could be recovered from
# ---------------------------------------------------------------------


def test_the_services_own_message_reaches_the_model():
    """
    GitHub said exactly how to fix the call. We threw it away.

    The agent loop exists so a model can read a failure and correct
    itself. It cannot correct anything from "the supplied data is
    invalid".
    """

    error = ToolError(
        code=ErrorCode.VALIDATION_ERROR,
        message="GitHub rejected the request because the supplied data is invalid.",
        source="service",
        status_code=422,
        details={
            "message": "Query must include 'is:issue' or 'is:pull-request'",
        },
    )

    payload = error.to_tool_payload()

    assert (
        payload["error"]["detail"]
        == "Query must include 'is:issue' or 'is:pull-request'"
    )


def test_a_detail_that_only_repeats_the_message_is_dropped():
    # No point spending context on the same sentence twice.
    error = ToolError(
        code=ErrorCode.NOT_FOUND,
        message="Not found.",
        details={"message": "Not found."},
    )

    assert "detail" not in error.to_tool_payload()["error"]


def test_our_own_errors_carry_no_detail():
    error = ToolError(
        code=ErrorCode.PERMISSION_DENIED,
        message="This agent may not use that tool.",
    )

    assert "detail" not in error.to_tool_payload()["error"]


def test_a_huge_service_message_is_bounded():
    # This lands in the model's context on every failure.
    error = ToolError(
        code=ErrorCode.SERVICE_ERROR,
        message="The service failed.",
        details={"message": "x" * 5000},
    )

    assert len(error.to_tool_payload()["error"]["detail"]) <= 300
