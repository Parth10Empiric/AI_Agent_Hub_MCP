from mcp.server import MCPServer

from .service import GitHubService
from .tool_helpers import github_tool

github = GitHubService()

    
def register_github_tools(mcp: MCPServer):

    @mcp.tool()
    @github_tool
    async def github_get_authenticated_user() -> dict:
        """Get the GitHub account associated with the configured token."""

        user = await github.get_authenticated_user()
        
        return {
            "login": user.get("login"),
            "name": user.get("name"),
            "email": user.get("email"),
            "public_repositories": user.get("public_repos"),
            "followers": user.get("followers"),
            "following": user.get("following"),
            "profile_url": user.get("html_url"),
        }

    @mcp.tool()
    @github_tool
    async def github_list_repositories(
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict] | dict:
        """List GitHub repositories accessible to the authenticated user."""

        repositories = await github.list_repositories(
            page=page,
            per_page=per_page,
        )
        
        return [
            {
                "name": repo.get("name"),
                "full_name": repo.get("full_name"),
                "private": repo.get("private"),
                "description": repo.get("description"),
                "default_branch": repo.get("default_branch"),
                "html_url": repo.get("html_url"),
                "updated_at": repo.get("updated_at"),
            }
            for repo in repositories
        ]

    @mcp.tool()
    @github_tool
    async def github_get_repository(
        owner: str,
        repo: str,
    ) -> dict:
        """Get information about a GitHub repository."""

        repository = await github.get_repository(
            owner=owner,
            repo=repo,
        )
        
        return {
            "name": repository.get("name"),
            "full_name": repository.get("full_name"),
            "description": repository.get("description"),
            "private": repository.get("private"),
            "default_branch": repository.get("default_branch"),
            "language": repository.get("language"),
            "stars": repository.get("stargazers_count"),
            "forks": repository.get("forks_count"),
            "open_issues": repository.get("open_issues_count"),
            "html_url": repository.get("html_url"),
        }

    @mcp.tool()
    @github_tool
    async def github_list_commits(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict] | dict:
        """List recent commits for a GitHub repository."""

        commits = await github.list_commits(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )
        
        return [
            {
                "sha": commit.get("sha"),
                "message": commit.get(
                    "commit", {}
                ).get("message", ""),
                "author": commit.get(
                    "commit", {}
                ).get("author", {}).get("name"),
                "date": commit.get(
                    "commit", {}
                ).get("author", {}).get("date"),
                "url": commit.get("html_url"),
            }
            for commit in commits
        ]


    @mcp.tool()
    @github_tool
    async def github_get_file(
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> dict:
        """Get metadata and content information for a repository file."""

        file_data = await github.get_file(
            owner=owner,
            repo=repo,
            path=path,
            ref=ref,
        )
        
        return {
            "name": file_data.get("name"),
            "path": file_data.get("path"),
            "type": file_data.get("type"),
            "size": file_data.get("size"),
            "sha": file_data.get("sha"),
            "download_url": file_data.get("download_url"),
            "html_url": file_data.get("html_url"),
            "encoding": file_data.get("encoding"),
            "content": file_data.get("content"),
        }


    @mcp.tool()
    @github_tool
    async def github_create_issue(
        owner: str,
        repo: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        confirm: bool = False,
    ) -> dict:
        """Create a GitHub issue.

        Use this only when the user explicitly wants
        to create an issue.
        """
        
        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Updating a GitHub issue requires "
                        "explicit user confirmation."
                    ),
                },
            }
            
        issue = await github.create_issue(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            labels=labels,
            assignees=assignees,
        )
        
        return {
            "success": True,
            "issue": {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "url": issue.get("html_url"),
            },
        }
            
    @mcp.tool()
    @github_tool
    async def github_list_branches(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List branches in a GitHub repository."""

        branches = await github.list_branches(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )
        
        return {
            "success": True,
            "branches": [
                {
                    "name": branch.get("name"),
                    "sha": branch.get("commit", {}).get("sha"),
                }
                for branch in branches
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_get_branch(
        owner: str,
        repo: str,
        branch: str,
    ) -> dict:
        """Get details about a specific GitHub branch."""

        data = await github.get_branch(
            owner=owner,
            repo=repo,
            branch=branch,
        )
        
        return {
            "success": True,
            "branch": {
                "name": data.get("name"),
                "protected": data.get("protected"),
                "commit_sha": data.get(
                    "commit", {}
                ).get("sha"),
                "commit_url": data.get(
                    "commit", {}
                ).get("url"),
            },
        }

            
    @mcp.tool()
    @github_tool
    async def github_list_issues(
        owner: str,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
        only_issues: bool = True,
    ) -> dict:
        """List issues in a GitHub repository.

        GitHub's issues endpoint can also return pull requests.
        Set only_issues=False to include those pull requests.
        """

        items = await github.list_issues(
            owner=owner,
            repo=repo,
            state=state,
            page=page,
            per_page=per_page,
        )
        
        if only_issues:
            items = [
                item
                for item in items
                if "pull_request" not in item
            ]
            
        return {
            "success": True,
            "issues": [
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "author": item.get(
                        "user", {}
                    ).get("login"),
                    "comments": item.get("comments"),
                    "url": item.get("html_url"),
                    "labels": [
                        label.get("name")
                        for label in item.get(
                            "labels", []
                        )
                    ],
                }
                for item in items
            ],
            "page": page,
            "per_page": per_page,
        }


    @mcp.tool()
    @github_tool
    async def github_get_issue(
        owner: str,
        repo: str,
        issue_number: int,
    ) -> dict:
        """Get a specific GitHub issue."""

        issue = await github.get_issue(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
        )

        return {
            "success": True,
            "issue": {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "body": issue.get("body"),
                "state": issue.get("state"),
                "author": issue.get(
                    "user", {}
                ).get("login"),
                "comments": issue.get("comments"),
                "url": issue.get("html_url"),
            },
        }
            
    @mcp.tool()
    @github_tool
    async def github_update_issue(
        owner: str,
        repo: str,
        issue_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        confirm: bool = False,
    ) -> dict:
        """Update a GitHub issue.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Updating a GitHub issue requires "
                        "explicit user confirmation."
                    ),
                },
            }

        issue = await github.update_issue(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            title=title,
            body=body,
            state=state,
            labels=labels,
            assignees=assignees,
        )
        
        return {
            "success": True,
            "issue": {
                "number": issue.get("number"),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "url": issue.get("html_url"),
            },
        }
            
    @mcp.tool()
    @github_tool
    async def github_add_issue_comment(
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
        confirm: bool = False,
    ) -> dict:
        """Add a comment to a GitHub issue.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Adding a GitHub comment requires "
                        "explicit user confirmation."
                    ),
                },
            }

        comment = await github.add_issue_comment(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            body=body,
        )
        
        return {
            "success": True,
            "comment": {
                "id": comment.get("id"),
                "body": comment.get("body"),
                "author": comment.get(
                    "user", {}
                ).get("login"),
                "url": comment.get("html_url"),
            },
        }

            
    @mcp.tool()
    @github_tool
    async def github_list_pull_requests(
        owner: str,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List pull requests in a repository."""

        pulls = await github.list_pull_requests(
            owner=owner,
            repo=repo,
            state=state,
            page=page,
            per_page=per_page,
        )
        
        return {
            "success": True,
            "pull_requests": [
                {
                    "number": pull.get("number"),
                    "title": pull.get("title"),
                    "state": pull.get("state"),
                    "draft": pull.get("draft"),
                    "author": pull.get(
                        "user", {}
                    ).get("login"),
                    "head": pull.get(
                        "head", {}
                    ).get("ref"),
                    "base": pull.get(
                        "base", {}
                    ).get("ref"),
                    "url": pull.get("html_url"),
                }
                for pull in pulls
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_get_pull_request(
        owner: str,
        repo: str,
        pull_number: int,
    ) -> dict:
        """Get details about a specific pull request."""

        pull = await github.get_pull_request(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
        )
        
        return {
            "success": True,
            "pull_request": {
                "number": pull.get("number"),
                "title": pull.get("title"),
                "body": pull.get("body"),
                "state": pull.get("state"),
                "draft": pull.get("draft"),
                "merged": pull.get("merged"),
                "author": pull.get(
                    "user", {}
                ).get("login"),
                "head": pull.get(
                    "head", {}
                ).get("ref"),
                "base": pull.get(
                    "base", {}
                ).get("ref"),
                "url": pull.get("html_url"),
            },
        }

            
    @mcp.tool()
    @github_tool
    async def github_create_pull_request(
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str | None = None,
        draft: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Create a pull request.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Creating a pull request requires "
                        "explicit user confirmation."
                    ),
                },
            }

        pull = await github.create_pull_request(
            owner=owner,
            repo=repo,
            title=title,
            head=head,
            base=base,
            body=body,
            draft=draft,
        )
        
        return {
            "success": True,
            "pull_request": {
                "number": pull.get("number"),
                "title": pull.get("title"),
                "state": pull.get("state"),
                "draft": pull.get("draft"),
                "url": pull.get("html_url"),
            },
        }

            
    @mcp.tool()
    @github_tool
    async def github_search_repositories(
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """Search GitHub repositories.

        The query can use GitHub search syntax, for example:
        'django language:python'
        """

        result = await github.search_repositories(
            query=query,
            page=page,
            per_page=per_page,
        )
        
        return {
            "success": True,
            "total_count": result.get(
                "total_count"
            ),
            "repositories": [
                {
                    "name": item.get("name"),
                    "full_name": item.get("full_name"),
                    "description": item.get(
                        "description"
                    ),
                    "private": item.get("private"),
                    "stars": item.get(
                        "stargazers_count"
                    ),
                    "language": item.get("language"),
                    "url": item.get("html_url"),
                }
                for item in result.get(
                    "items", []
                )
            ],
            "page": page,
            "per_page": per_page,
        }


    @mcp.tool()
    @github_tool
    async def github_search_issues(
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """Search GitHub issues and pull requests."""
        
        result = await github.search_issues(
            query=query,
            page=page,
            per_page=per_page,
        )
        
        return {
            "success": True,
            "total_count": result.get(
                "total_count"
            ),
            "items": [
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "repository": item.get(
                        "repository_url"
                    ),
                    "author": item.get(
                        "user", {}
                    ).get("login"),
                    "url": item.get("html_url"),
                    "is_pull_request": (
                        "pull_request" in item
                    ),
                }
                for item in result.get(
                    "items", []
                )
            ],
            "page": page,
            "per_page": per_page,
        }


    @mcp.tool()
    @github_tool
    async def github_search_code(
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """Search GitHub code using GitHub search syntax."""

        result = await github.search_code(
            query=query,
            page=page,
            per_page=per_page,
        )
        
        return {
            "success": True,
            "total_count": result.get(
                "total_count"
            ),
            "files": [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "sha": item.get("sha"),
                    "repository": item.get(
                        "repository", {}
                    ).get("full_name"),
                    "url": item.get("html_url"),
                }
                for item in result.get(
                    "items", []
                )
            ],
            "page": page,
            "per_page": per_page,
        }

