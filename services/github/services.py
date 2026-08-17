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

    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")

        if not self.token:
            raise ValueError(
                "GITHUB_TOKEN is missing. "
                "Add it to your .env file."
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

    async def get_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> dict:
        if not path.strip():
            raise ValueError("File path cannot be empty.")

        params = {}

        if ref:
            params["ref"] = ref

        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            params=params,
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

        return await self._request(
            "GET",
            "/search/issues",
            params={
                "q": query,
                "page": page,
                "per_page": per_page,
            },
        )

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
        
    