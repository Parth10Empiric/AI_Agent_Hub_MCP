import os
from typing import Any

import httpx
from dotenv import load_dotenv

from .errors import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubValidationError,
)


load_dotenv()


class GitHubService:
    BASE_URL = "https://api.github.com"
    API_VERSION = "2022-11-28"

    def __init__(self, token: str | None = None):
        """
        PHASE 5.5: the token is now a PARAMETER, not a global.

        Passed in, it is the calling user's own OAuth token, resolved
        by the backend and delivered in the MCP request metadata.

        Omitted, it falls back to .env - which keeps the CLI
        (ai_client.py), test_github.py and the whole local development
        loop working exactly as before. In production that fallback is
        refused before this line is ever reached; see
        core.tenancy.env_credentials_allowed.
        """

        self.token = token or os.getenv("GITHUB_TOKEN")

        if not self.token:
            raise ValueError(
                "No GitHub credentials. Connect GitHub in Agent Hub, "
                "or set GITHUB_TOKEN for local use."
            )

        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": self.API_VERSION,
            "User-Agent": "personal-mcp-server",
        }

        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=self.headers,
            timeout=30.0,
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    def _map_error(
        self,
        response: httpx.Response,
    ) -> GitHubAPIError:
        status = response.status_code

        try:
            data = response.json()
        except Exception:
            data = {}

        message = data.get(
            "message",
            response.text or "Unknown GitHub API error",
        )

        details = {
            "status_code": status,
            "message": message,
            "documentation_url": data.get(
                "documentation_url"
            ),
            "errors": data.get("errors", []),
        }

        if status == 401:
            return GitHubAuthenticationError(
                "GitHub authentication failed. "
                "Check your GITHUB_TOKEN.",
                status_code=status,
                details=details,
            )

        if status == 403:
            remaining = response.headers.get(
                "X-RateLimit-Remaining"
            )

            if remaining == "0":
                return GitHubRateLimitError(
                    "GitHub API rate limit exceeded.",
                    status_code=status,
                    details={
                        **details,
                        "rate_limit_remaining": remaining,
                        "rate_limit_reset": response.headers.get(
                            "X-RateLimit-Reset"
                        ),
                        "retry_after": response.headers.get(
                            "Retry-After"
                        ),
                    },
                )

            return GitHubPermissionError(
                "GitHub denied permission for this operation.",
                status_code=status,
                details=details,
            )

        if status == 404:
            return GitHubNotFoundError(
                "GitHub resource was not found or "
                "is not accessible with this token.",
                status_code=status,
                details=details,
            )

        if status == 422:
            return GitHubValidationError(
                "GitHub rejected the request because "
                "the supplied data is invalid.",
                status_code=status,
                details=details,
            )

        if status == 429:
            return GitHubRateLimitError(
                "GitHub rate limit exceeded.",
                status_code=status,
                details={
                    **details,
                    "retry_after": response.headers.get(
                        "Retry-After"
                    ),
                },
            )

        if 500 <= status <= 599:
            return GitHubServerError(
                "GitHub returned a server-side error.",
                status_code=status,
                details=details,
            )

        return GitHubAPIError(
            message,
            status_code=status,
            details=details,
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> Any:
        try:
            response = await self.client.request(
                method,
                endpoint,
                **kwargs,
            )

        except httpx.HTTPError as exc:
            raise GitHubAPIError(
                f"GitHub request failed: {exc}"
            ) from exc

        if response.is_error:
            raise self._map_error(response)

        if response.status_code == 204:
            return None

        return response.json()

    async def get_authenticated_user(self) -> dict:
        return await self._request(
            "GET",
            "/user",
        )

    async def list_repositories(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/user/repos",
            params={
                "page": page,
                "per_page": per_page,
                "sort": "updated",
            },
        )

    async def get_repository(
        self,
        owner: str,
        repo: str,
    ) -> dict:
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}",
        )

    async def list_commits(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits",
            params={
                "page": page,
                "per_page": per_page,
            },
        )

    async def get_contents(
        self,
        owner: str,
        repo: str,
        path: str = "",
        ref: str | None = None,
    ) -> dict | list:
        """
        One path in a repository - a file OR a directory.

        The GitHub endpoint behind this returns two different shapes:

            a file       an object
            a directory  an ARRAY of entries

        This method returns whichever arrived, unchanged, and the
        caller decides. Pretending it is always an object is what broke
        it: `github_get_file(path="/")` called `.get()` on a list and
        died with "'list' object has no attribute 'get'", three times
        over because a service_error on a read looks retryable.

        An empty path is the repository ROOT, which is a legitimate
        request - it is how anyone finds out what is in a repo. It used
        to raise ValueError, so the one call that answers "what files
        are here?" was rejected before it was made.
        """

        params = {}

        if ref:
            params["ref"] = ref

        # "/" and "" both mean the root. Normalised so the URL does not
        # end up with a double slash.
        clean = path.strip().strip("/")

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{clean}",
            params=params,
        )

    async def get_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> dict | list:
        """Kept for callers that predate get_contents."""

        if not path.strip():
            raise ValueError("File path cannot be empty.")

        return await self.get_contents(
            owner=owner,
            repo=repo,
            path=path,
            ref=ref,
        )

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict:
        if not title or not title.strip():
            raise ValueError(
                "Issue title cannot be empty."
            )

        if len(title.strip()) > 256:
            raise ValueError(
                "Issue title is too long."
            )

        payload: dict[str, Any] = {
            "title": title.strip(),
        }

        if body is not None:
            payload["body"] = body

        if labels:
            payload["labels"] = labels

        if assignees:
            payload["assignees"] = assignees

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json=payload,
        )
        
    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        if state not in {"open", "closed", "all"}:
            raise ValueError(
                "state must be 'open', 'closed', or 'all'."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            params={
                "state": state,
                "page": page,
                "per_page": per_page,
                "sort": "updated",
                "direction": "desc",
            },
        )

    async def get_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> dict:
        if issue_number < 1:
            raise ValueError(
                "Issue number must be greater than 0."
            )

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
        )

    async def update_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict:
        if issue_number < 1:
            raise ValueError(
                "Issue number must be greater than 0."
            )

        if state is not None and state not in {
            "open",
            "closed",
        }:
            raise ValueError(
                "state must be 'open' or 'closed'."
            )

        payload = {}

        if title is not None:
            if not title.strip():
                raise ValueError(
                    "Issue title cannot be empty."
                )

            payload["title"] = title.strip()

        if body is not None:
            payload["body"] = body

        if state is not None:
            payload["state"] = state

        if labels is not None:
            payload["labels"] = labels

        if assignees is not None:
            payload["assignees"] = assignees

        if not payload:
            raise ValueError(
                "At least one field must be supplied."
            )

        return await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json=payload,
        )

    async def add_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict:
        if issue_number < 1:
            raise ValueError(
                "Issue number must be greater than 0."
            )

        if not body.strip():
            raise ValueError(
                "Comment body cannot be empty."
            )

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={
                "body": body,
            },
        )
        
    async def list_branches(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/branches",
            params={
                "page": page,
                "per_page": per_page,
            },
        )

    async def get_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
    ) -> dict:
        if not branch.strip():
            raise ValueError("Branch name cannot be empty.")

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/branches/{branch}",
        )
        
    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        if state not in {
            "open",
            "closed",
            "all",
        }:
            raise ValueError(
                "state must be 'open', 'closed', or 'all'."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={
                "state": state,
                "page": page,
                "per_page": per_page,
                "sort": "updated",
                "direction": "desc",
            },
        )

    async def get_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> dict:
        if pull_number < 1:
            raise ValueError(
                "Pull request number must be greater than 0."
            )

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pull_number}",
        )

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str | None = None,
        draft: bool = False,
    ) -> dict:
        if not title.strip():
            raise ValueError(
                "Pull request title cannot be empty."
            )

        if not head.strip():
            raise ValueError(
                "Head branch cannot be empty."
            )

        if not base.strip():
            raise ValueError(
                "Base branch cannot be empty."
            )

        payload = {
            "title": title.strip(),
            "head": head.strip(),
            "base": base.strip(),
            "draft": draft,
        }

        if body is not None:
            payload["body"] = body

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json=payload,
        )
        
    async def search_repositories(
        self,
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        if not query.strip():
            raise ValueError(
                "Repository search query cannot be empty."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/search/repositories",
            params={
                "q": query,
                "page": page,
                "per_page": per_page,
            },
        )

    @staticmethod
    def _qualified_issue_query(query: str) -> str:
        """
        GitHub REQUIRES `is:issue` or `is:pull-request` on this search.

        Without one it answers 422:

            "Query must include 'is:issue' or 'is:pull-request'"

        That is not a preference, it is a hard precondition of the
        endpoint, so the search cannot work unless something supplies
        it. Leaving it to the caller meant a perfectly reasonable
        `repo:owner/name` was rejected every time.

        `is:issue` is the default because the tool is called
        search_issues. A caller who wants pull requests says
        `is:pull-request` and this leaves the query alone.
        """

        lowered = query.lower()

        if "is:issue" in lowered or "is:pull-request" in lowered:
            return query

        return f"{query} is:issue".strip()

    async def search_issues(
        self,
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        if not query.strip():
            raise ValueError(
                "Issue search query cannot be empty."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        effective = self._qualified_issue_query(query)

        result = await self._request(
            "GET",
            "/search/issues",
            params={
                "q": effective,
                "page": page,
                "per_page": per_page,
            },
        )

        # What actually ran, when it is not what was asked for. The
        # caller - and the model reading the result - should never have
        # to guess whether their query was rewritten.
        if isinstance(result, dict) and effective != query:
            result = {**result, "effective_query": effective}

        return result

    async def search_code(
        self,
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        if not query.strip():
            raise ValueError(
                "Code search query cannot be empty."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/search/code",
            params={
                "q": query,
                "page": page,
                "per_page": per_page,
            },
        )
        
    
    # =================================================================
    # REPOSITORY MANAGEMENT
    # =================================================================
    #
    # Everything above this line reads. Everything below can change or
    # destroy an account, so each method validates its own input before
    # a request is made - a 422 from GitHub tells the model far less
    # than "Repository name cannot be empty."

    async def create_repository(
        self,
        name: str,
        description: str | None = None,
        private: bool = True,
        auto_init: bool = True,
        gitignore_template: str | None = None,
        license_template: str | None = None,
        organization: str | None = None,
    ) -> dict:
        """
        Create a repository, for the user or for an organization.

        `private` defaults to TRUE, which is the opposite of GitHub's
        own default. An agent that misunderstands one word should not
        be able to publish your code to the internet; the user asking
        for a public repo says so, and nothing is lost by making the
        safe case the quiet one.
        """

        if not name or not name.strip():
            raise ValueError("Repository name cannot be empty.")

        clean = name.strip()

        # GitHub silently rewrites anything else to a dash, so a repo
        # called "my repo" is created as "my-repo" and every follow-up
        # call the model makes with the original name 404s.
        if not all(c.isalnum() or c in "-._" for c in clean):
            raise ValueError(
                "Repository names may only contain letters, digits, "
                "'-', '_' and '.'. GitHub rewrites anything else, "
                f"so {clean!r} would not be the name you get back."
            )

        payload: dict[str, Any] = {
            "name": clean,
            "private": private,
            "auto_init": auto_init,
        }

        if description is not None:
            payload["description"] = description

        if gitignore_template:
            payload["gitignore_template"] = gitignore_template

        if license_template:
            payload["license_template"] = license_template

        # Two different endpoints, one for each kind of owner.
        endpoint = (
            f"/orgs/{organization}/repos"
            if organization
            else "/user/repos"
        )

        return await self._request("POST", endpoint, json=payload)

    async def update_repository(
        self,
        owner: str,
        repo: str,
        name: str | None = None,
        description: str | None = None,
        homepage: str | None = None,
        private: bool | None = None,
        default_branch: str | None = None,
        archived: bool | None = None,
    ) -> dict:
        payload: dict[str, Any] = {}

        if name is not None:
            if not name.strip():
                raise ValueError("Repository name cannot be empty.")
            payload["name"] = name.strip()

        if description is not None:
            payload["description"] = description

        if homepage is not None:
            payload["homepage"] = homepage

        if private is not None:
            payload["private"] = private

        if default_branch is not None:
            payload["default_branch"] = default_branch

        if archived is not None:
            payload["archived"] = archived

        if not payload:
            raise ValueError(
                "At least one field must be supplied."
            )

        return await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}",
            json=payload,
        )

    async def delete_repository(
        self,
        owner: str,
        repo: str,
    ) -> dict:
        """
        Delete a repository. There is no undo through the API.

        Returns a dict rather than None so the tool layer has something
        to report: the endpoint answers 204 with an empty body, and
        `_request` turns that into None, which reads to a model like
        the call did nothing.
        """

        await self._request("DELETE", f"/repos/{owner}/{repo}")

        return {"deleted": f"{owner}/{repo}"}

    async def create_fork(
        self,
        owner: str,
        repo: str,
        organization: str | None = None,
        name: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {}

        if organization:
            payload["organization"] = organization

        if name:
            payload["name"] = name.strip()

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/forks",
            json=payload,
        )

    async def list_organization_repositories(
        self,
        org: str,
        page: int = 1,
        per_page: int = 30,
        type: str = "all",
    ) -> list[dict]:
        if type not in {
            "all",
            "public",
            "private",
            "forks",
            "sources",
            "member",
        }:
            raise ValueError(
                "type must be one of: all, public, private, forks, "
                "sources, member."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/orgs/{org}/repos",
            params={
                "page": page,
                "per_page": per_page,
                "type": type,
                "sort": "updated",
            },
        )

    async def list_organizations(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/user/orgs",
            params={"page": page, "per_page": per_page},
        )

    async def get_user(self, username: str) -> dict:
        if not username.strip():
            raise ValueError("Username cannot be empty.")

        return await self._request(
            "GET",
            f"/users/{username.strip()}",
        )

    async def list_languages(
        self,
        owner: str,
        repo: str,
    ) -> dict:
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/languages",
        )

    async def list_topics(
        self,
        owner: str,
        repo: str,
    ) -> dict:
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/topics",
        )

    async def list_collaborators(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/collaborators",
            params={"page": page, "per_page": per_page},
        )

    async def add_collaborator(
        self,
        owner: str,
        repo: str,
        username: str,
        permission: str = "push",
    ) -> dict:
        """
        Give another person access to a repository.

        This is an ACCESS-CONTROL change, not an ordinary write - it
        hands a human being the ability to read, and usually to modify,
        your code. Classified ADMIN/HIGH for the same reason
        google_drive_create_permission is.
        """

        if not username.strip():
            raise ValueError("Username cannot be empty.")

        if permission not in {
            "pull",
            "triage",
            "push",
            "maintain",
            "admin",
        }:
            raise ValueError(
                "permission must be one of: pull, triage, push, "
                "maintain, admin."
            )

        result = await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/collaborators/{username.strip()}",
            json={"permission": permission},
        )

        # 201 returns an invitation; 204 (already a collaborator)
        # returns nothing at all. Both are success, and the caller
        # should be able to tell which happened.
        if result is None:
            return {
                "invited": False,
                "note": (
                    f"{username} already had access to "
                    f"{owner}/{repo}."
                ),
            }

        return result

    async def remove_collaborator(
        self,
        owner: str,
        repo: str,
        username: str,
    ) -> dict:
        if not username.strip():
            raise ValueError("Username cannot be empty.")

        await self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/collaborators/{username.strip()}",
        )

        return {"removed": username.strip()}

    # =================================================================
    # BRANCHES, COMMITS AND FILE CONTENTS
    # =================================================================

    async def get_commit(
        self,
        owner: str,
        repo: str,
        ref: str,
    ) -> dict:
        """One commit, including the per-file diff statistics."""

        if not ref.strip():
            raise ValueError("Commit ref cannot be empty.")

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits/{ref.strip()}",
        )

    async def compare_commits(
        self,
        owner: str,
        repo: str,
        base: str,
        head: str,
    ) -> dict:
        if not base.strip() or not head.strip():
            raise ValueError(
                "Both base and head refs are required."
            )

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/compare/"
            f"{base.strip()}...{head.strip()}",
        )

    async def list_tags(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/tags",
            params={"page": page, "per_page": per_page},
        )

    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
        from_branch: str | None = None,
    ) -> dict:
        """
        Create a branch.

        GitHub has no "create branch" endpoint - it has "create a git
        ref", which needs the SHA the new branch should point at. A
        caller who has to look that SHA up first will get it wrong, so
        this resolves it: the named source branch, or the repository's
        own default branch when none is given.

        Three requests instead of one is the right trade. The
        alternative is a tool that only works if the model already
        performed two other calls in the correct order.
        """

        if not branch.strip():
            raise ValueError("Branch name cannot be empty.")

        source = from_branch

        if not source:
            repository = await self.get_repository(owner, repo)
            source = repository.get("default_branch") or "main"

        head = await self.get_branch(
            owner=owner,
            repo=repo,
            branch=source,
        )

        sha = head.get("commit", {}).get("sha")

        if not sha:
            raise GitHubAPIError(
                f"Could not resolve the head commit of {source!r}."
            )

        created = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={
                "ref": f"refs/heads/{branch.strip()}",
                "sha": sha,
            },
        )

        # Which branch it was cut from is not in GitHub's reply, and it
        # is the one fact the user will want confirmed.
        return {**created, "created_from": source}

    async def delete_branch(
        self,
        owner: str,
        repo: str,
        branch: str,
    ) -> dict:
        if not branch.strip():
            raise ValueError("Branch name cannot be empty.")

        await self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/git/refs/heads/{branch.strip()}",
        )

        return {"deleted_branch": branch.strip()}

    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
    ) -> dict:
        """
        Commit a text file - creating it, or replacing it.

        Two details the GitHub API makes easy to get wrong, both
        handled here rather than pushed onto the caller:

        1. `content` must be base64. The caller passes ordinary text.

        2. REPLACING a file requires the blob SHA of the version being
           replaced. Without it GitHub answers 422 "sha wasn't
           supplied", and with the WRONG one it answers 409 - which is
           GitHub's optimistic lock, and the reason it exists: two
           agents editing the same file must not silently overwrite
           each other. When no sha is given this looks the file up, so
           the common case works, and a caller who read the file
           earlier can still pass the sha they saw to get the lock.
        """

        import base64

        if not path.strip():
            raise ValueError("File path cannot be empty.")

        if not message.strip():
            raise ValueError("Commit message cannot be empty.")

        clean = path.strip().strip("/")

        payload: dict[str, Any] = {
            "message": message.strip(),
            "content": base64.b64encode(
                content.encode("utf-8")
            ).decode("ascii"),
        }

        if branch:
            payload["branch"] = branch.strip()

        if sha:
            payload["sha"] = sha

        else:
            # Does it already exist? A 404 here is the ANSWER, not a
            # failure - it means "new file", which needs no sha.
            try:
                existing = await self.get_contents(
                    owner=owner,
                    repo=repo,
                    path=clean,
                    ref=branch,
                )

                if isinstance(existing, dict) and existing.get("sha"):
                    payload["sha"] = existing["sha"]

                elif isinstance(existing, list):
                    raise ValueError(
                        f"{clean!r} is a directory, not a file."
                    )

            except GitHubNotFoundError:
                pass

        return await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{clean}",
            json=payload,
        )

    async def delete_file(
        self,
        owner: str,
        repo: str,
        path: str,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
    ) -> dict:
        """Delete a file. The blob SHA is looked up when not supplied."""

        if not path.strip():
            raise ValueError("File path cannot be empty.")

        if not message.strip():
            raise ValueError("Commit message cannot be empty.")

        clean = path.strip().strip("/")

        blob_sha = sha

        if not blob_sha:
            existing = await self.get_contents(
                owner=owner,
                repo=repo,
                path=clean,
                ref=branch,
            )

            if isinstance(existing, list):
                raise ValueError(
                    f"{clean!r} is a directory. This tool deletes one "
                    "file at a time."
                )

            blob_sha = existing.get("sha")

        if not blob_sha:
            raise ValueError(
                f"Could not resolve the current SHA of {clean!r}."
            )

        payload: dict[str, Any] = {
            "message": message.strip(),
            "sha": blob_sha,
        }

        if branch:
            payload["branch"] = branch.strip()

        return await self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/contents/{clean}",
            json=payload,
        )

    async def get_readme(
        self,
        owner: str,
        repo: str,
        ref: str | None = None,
    ) -> dict:
        params = {"ref": ref} if ref else {}

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/readme",
            params=params,
        )

    # =================================================================
    # ISSUES - COMMENTS AND LABELS
    # =================================================================

    async def list_issue_comments(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        if issue_number < 1:
            raise ValueError(
                "Issue number must be greater than 0."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            params={"page": page, "per_page": per_page},
        )

    async def list_labels(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/labels",
            params={"page": page, "per_page": per_page},
        )

    async def create_label(
        self,
        owner: str,
        repo: str,
        name: str,
        color: str = "ededed",
        description: str | None = None,
    ) -> dict:
        if not name.strip():
            raise ValueError("Label name cannot be empty.")

        # GitHub wants six hex digits and no leading '#'. Sending "#f00"
        # is a 422 that says only "Validation Failed", so it is worth
        # catching the two common shapes here instead.
        clean_color = color.strip().lstrip("#").lower()

        if len(clean_color) != 6 or any(
            c not in "0123456789abcdef" for c in clean_color
        ):
            raise ValueError(
                "color must be six hexadecimal digits, "
                "for example 'd73a4a'."
            )

        payload: dict[str, Any] = {
            "name": name.strip(),
            "color": clean_color,
        }

        if description is not None:
            payload["description"] = description

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/labels",
            json=payload,
        )

    async def add_issue_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> list[dict]:
        if issue_number < 1:
            raise ValueError(
                "Issue number must be greater than 0."
            )

        if not labels:
            raise ValueError(
                "At least one label must be supplied."
            )

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )

    async def remove_issue_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        label: str,
    ) -> dict:
        if issue_number < 1:
            raise ValueError(
                "Issue number must be greater than 0."
            )

        if not label.strip():
            raise ValueError("Label name cannot be empty.")

        await self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/issues/{issue_number}"
            f"/labels/{label.strip()}",
        )

        return {
            "issue_number": issue_number,
            "removed_label": label.strip(),
        }

    async def list_assignees(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/assignees",
            params={"page": page, "per_page": per_page},
        )

    async def list_milestones(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        if state not in {"open", "closed", "all"}:
            raise ValueError(
                "state must be 'open', 'closed', or 'all'."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/milestones",
            params={
                "state": state,
                "page": page,
                "per_page": per_page,
            },
        )

    # =================================================================
    # PULL REQUESTS - REVIEW AND MERGE
    # =================================================================

    async def update_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        base: str | None = None,
    ) -> dict:
        if pull_number < 1:
            raise ValueError(
                "Pull request number must be greater than 0."
            )

        if state is not None and state not in {"open", "closed"}:
            raise ValueError(
                "state must be 'open' or 'closed'."
            )

        payload: dict[str, Any] = {}

        if title is not None:
            if not title.strip():
                raise ValueError(
                    "Pull request title cannot be empty."
                )
            payload["title"] = title.strip()

        if body is not None:
            payload["body"] = body

        if state is not None:
            payload["state"] = state

        if base is not None:
            payload["base"] = base.strip()

        if not payload:
            raise ValueError(
                "At least one field must be supplied."
            )

        return await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/pulls/{pull_number}",
            json=payload,
        )

    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: str = "merge",
        sha: str | None = None,
    ) -> dict:
        """
        Merge a pull request into its base branch.

        `sha` is GitHub's optimistic lock: pass the head SHA you saw
        and the merge is refused if somebody pushed since. Worth using
        whenever a human approved the merge of a diff they READ - what
        they agreed to and what would land are otherwise not
        necessarily the same code.
        """

        if pull_number < 1:
            raise ValueError(
                "Pull request number must be greater than 0."
            )

        if merge_method not in {"merge", "squash", "rebase"}:
            raise ValueError(
                "merge_method must be 'merge', 'squash' or 'rebase'."
            )

        payload: dict[str, Any] = {"merge_method": merge_method}

        if commit_title:
            payload["commit_title"] = commit_title

        if commit_message:
            payload["commit_message"] = commit_message

        if sha:
            payload["sha"] = sha

        return await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/merge",
            json=payload,
        )

    async def list_pull_request_files(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        if pull_number < 1:
            raise ValueError(
                "Pull request number must be greater than 0."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/files",
            params={"page": page, "per_page": per_page},
        )

    async def list_pull_request_commits(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        if pull_number < 1:
            raise ValueError(
                "Pull request number must be greater than 0."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/commits",
            params={"page": page, "per_page": per_page},
        )

    async def list_pull_request_reviews(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        if pull_number < 1:
            raise ValueError(
                "Pull request number must be greater than 0."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            params={"page": page, "per_page": per_page},
        )

    async def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        event: str = "COMMENT",
        body: str | None = None,
    ) -> dict:
        """
        Review a pull request: comment, approve, or request changes.

        APPROVE is the one that matters. An approval is a human
        signature on someone else's code, and on a protected branch it
        is what unlocks the merge button - which is why the tool that
        calls this asks first.
        """

        if pull_number < 1:
            raise ValueError(
                "Pull request number must be greater than 0."
            )

        normalized = event.strip().upper()

        if normalized not in {
            "APPROVE",
            "REQUEST_CHANGES",
            "COMMENT",
        }:
            raise ValueError(
                "event must be 'APPROVE', 'REQUEST_CHANGES' "
                "or 'COMMENT'."
            )

        # GitHub's own rule, and its error message for breaking it is
        # unhelpfully generic.
        if normalized != "APPROVE" and not (body or "").strip():
            raise ValueError(
                f"A {normalized} review requires a body explaining it."
            )

        payload: dict[str, Any] = {"event": normalized}

        if body is not None:
            payload["body"] = body

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            json=payload,
        )

    # =================================================================
    # RELEASES, ACTIONS, GISTS, STARS AND NOTIFICATIONS
    # =================================================================

    async def list_releases(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/releases",
            params={"page": page, "per_page": per_page},
        )

    async def get_latest_release(
        self,
        owner: str,
        repo: str,
    ) -> dict:
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/releases/latest",
        )

    async def get_release(
        self,
        owner: str,
        repo: str,
        release_id: int,
    ) -> dict:
        if release_id < 1:
            raise ValueError(
                "Release id must be greater than 0."
            )

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/releases/{release_id}",
        )

    async def create_release(
        self,
        owner: str,
        repo: str,
        tag_name: str,
        name: str | None = None,
        body: str | None = None,
        draft: bool = False,
        prerelease: bool = False,
        target_commitish: str | None = None,
        generate_release_notes: bool = False,
    ) -> dict:
        """
        Publish a release.

        Not a quiet write: publishing notifies watchers and, for a
        package repository, is what makes a version installable by
        other people. `draft=True` is the rehearsal.
        """

        if not tag_name.strip():
            raise ValueError("Tag name cannot be empty.")

        payload: dict[str, Any] = {
            "tag_name": tag_name.strip(),
            "draft": draft,
            "prerelease": prerelease,
            "generate_release_notes": generate_release_notes,
        }

        if name is not None:
            payload["name"] = name

        if body is not None:
            payload["body"] = body

        if target_commitish:
            payload["target_commitish"] = target_commitish.strip()

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/releases",
            json=payload,
        )

    async def list_workflows(
        self,
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/workflows",
            params={"page": page, "per_page": per_page},
        )

    async def list_workflow_runs(
        self,
        owner: str,
        repo: str,
        workflow_id: str | None = None,
        branch: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        params: dict[str, Any] = {
            "page": page,
            "per_page": per_page,
        }

        if branch:
            params["branch"] = branch.strip()

        if status:
            params["status"] = status.strip()

        # Runs of ONE workflow, or of every workflow in the repo. The
        # id may be numeric or the file name ("ci.yml"); GitHub accepts
        # both on the same path segment.
        endpoint = (
            f"/repos/{owner}/{repo}/actions/workflows/"
            f"{workflow_id.strip()}/runs"
            if workflow_id
            else f"/repos/{owner}/{repo}/actions/runs"
        )

        return await self._request("GET", endpoint, params=params)

    async def get_workflow_run(
        self,
        owner: str,
        repo: str,
        run_id: int,
    ) -> dict:
        if run_id < 1:
            raise ValueError("Run id must be greater than 0.")

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}",
        )

    async def list_gists(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/gists",
            params={"page": page, "per_page": per_page},
        )

    async def create_gist(
        self,
        files: dict[str, str],
        description: str | None = None,
        public: bool = False,
    ) -> dict:
        """
        Create a gist from {filename: content}.

        `public` defaults to False for the same reason repositories do:
        a public gist is a permanent, indexed, publicly readable
        document, and "secret" is the recoverable mistake.
        """

        if not files:
            raise ValueError(
                "At least one file is required, as "
                "{'name.txt': 'contents'}."
            )

        payload: dict[str, Any] = {
            "public": public,
            "files": {
                name: {"content": content}
                for name, content in files.items()
            },
        }

        if description is not None:
            payload["description"] = description

        return await self._request("POST", "/gists", json=payload)

    async def list_starred_repositories(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/user/starred",
            params={"page": page, "per_page": per_page},
        )

    async def star_repository(
        self,
        owner: str,
        repo: str,
    ) -> dict:
        await self._request(
            "PUT",
            f"/user/starred/{owner}/{repo}",
            headers={"Content-Length": "0"},
        )

        return {"starred": f"{owner}/{repo}"}

    async def unstar_repository(
        self,
        owner: str,
        repo: str,
    ) -> dict:
        await self._request(
            "DELETE",
            f"/user/starred/{owner}/{repo}",
        )

        return {"unstarred": f"{owner}/{repo}"}

    async def list_notifications(
        self,
        all: bool = False,
        participating: bool = False,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/notifications",
            params={
                # httpx renders Python bools as "true"/"false", which
                # is what GitHub wants here.
                "all": all,
                "participating": participating,
                "page": page,
                "per_page": per_page,
            },
        )

    async def search_users(
        self,
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        if not query.strip():
            raise ValueError(
                "User search query cannot be empty."
            )

        page = max(1, page)
        per_page = max(1, min(per_page, 100))

        return await self._request(
            "GET",
            "/search/users",
            params={
                "q": query,
                "page": page,
                "per_page": per_page,
            },
        )
