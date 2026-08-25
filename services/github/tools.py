from mcp.server import MCPServer

from core.tenancy import service_proxy

from .services import GitHubService
from .tool_helpers import github_tool


# NOT a GitHubService any more - a proxy that resolves to whichever
# instance belongs to the request being handled (Phase 5.5).
#
# Every tool below still writes `await github.something(...)` and none
# of them changed. That is the whole point of the proxy: the name stays,
# the object behind it stops being shared.
github = service_proxy("github", lambda token: GitHubService(token=token))

    
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
        path: str = "",
        ref: str | None = None,
    ) -> dict:
        """
        Read a repository file, or list a directory.

        Pass a file path to get that file's metadata and content. Pass
        a directory path - or "" / "/" for the repository root - to
        list what is inside it. Listing the root first is how you find
        out what a repository contains.

        The reply always has a "type" field: "file" or "dir". A "dir"
        reply has "entries"; a "file" reply has "content".
        """

        data = await github.get_contents(
            owner=owner,
            repo=repo,
            path=path,
            ref=ref,
        )

        # A DIRECTORY. GitHub answers with an array, and this branch is
        # the whole bug fix: the old code called .get() on it and died
        # with "'list' object has no attribute 'get'" - a service_error,
        # which on a read looks retryable, so it did that three times.
        #
        # There was also no other way to list a directory, so the model
        # had improvised `path="/"` precisely because it needed this.
        if isinstance(data, list):
            return {
                "type": "dir",
                "path": path.strip("/"),
                "count": len(data),
                "entries": [
                    {
                        "name": entry.get("name"),
                        "path": entry.get("path"),
                        "type": entry.get("type"),
                        "size": entry.get("size"),
                    }
                    for entry in data
                    if isinstance(entry, dict)
                ],
            }

        if not isinstance(data, dict):
            return {
                "type": "unknown",
                "path": path,
                "note": "GitHub returned an unexpected shape.",
            }

        return {
            "name": data.get("name"),
            "path": data.get("path"),
            "type": data.get("type"),
            "size": data.get("size"),
            "sha": data.get("sha"),
            "download_url": data.get("download_url"),
            "html_url": data.get("html_url"),
            "encoding": data.get("encoding"),
            "content": data.get("content"),
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
        """
        Search GitHub issues and pull requests.

        Uses GitHub search syntax, e.g. "repo:owner/name is:open bug".
        GitHub requires the query to say which it is searching, so
        `is:issue` is added automatically when neither `is:issue` nor
        `is:pull-request` is present.
        """
        
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



    # =================================================================
    # REPOSITORY MANAGEMENT
    # =================================================================
    #
    # `confirm` on every mutating tool is the same guard the existing
    # write tools use, and it is deliberately NOT the approval system.
    # Approval lives in the agent (agent/permissions.py) and can be
    # switched off per tool; this is a second, dumber gate that lives
    # in the server and cannot be. Two independent locks on the tools
    # that change somebody's real account is the correct number.

    @mcp.tool()
    @github_tool
    async def github_create_repository(
        name: str,
        description: str | None = None,
        private: bool = True,
        auto_init: bool = True,
        gitignore_template: str | None = None,
        license_template: str | None = None,
        organization: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Create a new GitHub repository.

        Creates it under the authenticated user unless `organization`
        is given. New repositories are PRIVATE unless you pass
        private=False. Set auto_init=True to get an initial commit and
        a README, which is what makes the repository usable straight
        away - an empty repository has no branches, so nothing can be
        committed to it until something is.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Creating a GitHub repository requires "
                        "explicit user confirmation."
                    ),
                },
            }

        repository = await github.create_repository(
            name=name,
            description=description,
            private=private,
            auto_init=auto_init,
            gitignore_template=gitignore_template,
            license_template=license_template,
            organization=organization,
        )

        return {
            "success": True,
            "repository": {
                "name": repository.get("name"),
                "full_name": repository.get("full_name"),
                "private": repository.get("private"),
                "default_branch": repository.get("default_branch"),
                "clone_url": repository.get("clone_url"),
                "ssh_url": repository.get("ssh_url"),
                "html_url": repository.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_update_repository(
        owner: str,
        repo: str,
        name: str | None = None,
        description: str | None = None,
        homepage: str | None = None,
        private: bool | None = None,
        default_branch: str | None = None,
        archived: bool | None = None,
        confirm: bool = False,
    ) -> dict:
        """Update a GitHub repository's settings.

        Only the fields you supply are changed. Note that archiving a
        repository makes it read-only, and that changing `private` from
        True to False publishes every commit in its history.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Updating a GitHub repository requires "
                        "explicit user confirmation."
                    ),
                },
            }

        repository = await github.update_repository(
            owner=owner,
            repo=repo,
            name=name,
            description=description,
            homepage=homepage,
            private=private,
            default_branch=default_branch,
            archived=archived,
        )

        return {
            "success": True,
            "repository": {
                "name": repository.get("name"),
                "full_name": repository.get("full_name"),
                "description": repository.get("description"),
                "private": repository.get("private"),
                "archived": repository.get("archived"),
                "default_branch": repository.get("default_branch"),
                "html_url": repository.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_delete_repository(
        owner: str,
        repo: str,
        confirm: bool = False,
    ) -> dict:
        """Permanently delete a GitHub repository.

        THIS CANNOT BE UNDONE. Every issue, pull request, release and
        the entire commit history are destroyed with it. Only use this
        when the user has named the exact repository and clearly asked
        for it to be deleted.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Deleting {owner}/{repo} is irreversible and "
                        "requires explicit user confirmation."
                    ),
                },
            }

        result = await github.delete_repository(
            owner=owner,
            repo=repo,
        )

        return {"success": True, **result}

    @mcp.tool()
    @github_tool
    async def github_create_fork(
        owner: str,
        repo: str,
        organization: str | None = None,
        name: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Fork a GitHub repository into your own account.

        Forking is asynchronous on GitHub's side: the reply comes back
        immediately, but the fork may take a few moments to finish
        being built for a large repository.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Forking a repository requires explicit "
                        "user confirmation."
                    ),
                },
            }

        fork = await github.create_fork(
            owner=owner,
            repo=repo,
            organization=organization,
            name=name,
        )

        return {
            "success": True,
            "fork": {
                "full_name": fork.get("full_name"),
                "private": fork.get("private"),
                "default_branch": fork.get("default_branch"),
                "html_url": fork.get("html_url"),
            },
            "note": (
                "GitHub builds forks asynchronously; a large "
                "repository may take a moment to become usable."
            ),
        }

    @mcp.tool()
    @github_tool
    async def github_list_organizations(
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the GitHub organizations you belong to."""

        organizations = await github.list_organizations(
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "organizations": [
                {
                    "login": org.get("login"),
                    "description": org.get("description"),
                    "url": org.get("url"),
                }
                for org in organizations
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_list_organization_repositories(
        org: str,
        type: str = "all",
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List repositories belonging to a GitHub organization.

        `type` filters the list: all, public, private, forks, sources
        or member.
        """

        repositories = await github.list_organization_repositories(
            org=org,
            page=page,
            per_page=per_page,
            type=type,
        )

        return {
            "success": True,
            "repositories": [
                {
                    "name": repo.get("name"),
                    "full_name": repo.get("full_name"),
                    "private": repo.get("private"),
                    "description": repo.get("description"),
                    "language": repo.get("language"),
                    "updated_at": repo.get("updated_at"),
                    "html_url": repo.get("html_url"),
                }
                for repo in repositories
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_get_user(username: str) -> dict:
        """Get a GitHub user's public profile."""

        user = await github.get_user(username=username)

        return {
            "success": True,
            "user": {
                "login": user.get("login"),
                "name": user.get("name"),
                "company": user.get("company"),
                "location": user.get("location"),
                "bio": user.get("bio"),
                "public_repositories": user.get("public_repos"),
                "followers": user.get("followers"),
                "following": user.get("following"),
                "profile_url": user.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_search_users(
        query: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """Search for GitHub users.

        Uses GitHub search syntax, e.g. "location:berlin language:go".
        """

        result = await github.search_users(
            query=query,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "total_count": result.get("total_count"),
            "users": [
                {
                    "login": item.get("login"),
                    "type": item.get("type"),
                    "url": item.get("html_url"),
                }
                for item in result.get("items", [])
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_list_languages(
        owner: str,
        repo: str,
    ) -> dict:
        """List the programming languages used in a repository.

        Returns bytes of code per language, so the largest number is
        the repository's main language.
        """

        languages = await github.list_languages(
            owner=owner,
            repo=repo,
        )

        total = sum(languages.values()) or 1

        return {
            "success": True,
            "languages": [
                {
                    "language": language,
                    "bytes": size,
                    "percent": round(100 * size / total, 1),
                }
                for language, size in sorted(
                    languages.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            ],
        }

    @mcp.tool()
    @github_tool
    async def github_list_topics(
        owner: str,
        repo: str,
    ) -> dict:
        """List the topics (tags) on a GitHub repository."""

        data = await github.list_topics(owner=owner, repo=repo)

        return {
            "success": True,
            "topics": data.get("names", []),
        }

    @mcp.tool()
    @github_tool
    async def github_list_collaborators(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the people who have access to a repository."""

        collaborators = await github.list_collaborators(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "collaborators": [
                {
                    "login": person.get("login"),
                    "role": person.get("role_name"),
                    "permissions": person.get("permissions"),
                    "url": person.get("html_url"),
                }
                for person in collaborators
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_add_collaborator(
        owner: str,
        repo: str,
        username: str,
        permission: str = "push",
        confirm: bool = False,
    ) -> dict:
        """Give another GitHub user access to a repository.

        This grants a real human being access to your code. Permission
        levels: pull (read), triage, push (write), maintain, admin.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Granting {username} '{permission}' access to "
                        f"{owner}/{repo} requires explicit user "
                        "confirmation."
                    ),
                },
            }

        result = await github.add_collaborator(
            owner=owner,
            repo=repo,
            username=username,
            permission=permission,
        )

        return {
            "success": True,
            "username": username,
            "permission": permission,
            "invitation_id": result.get("id"),
            "note": result.get("note"),
        }

    @mcp.tool()
    @github_tool
    async def github_remove_collaborator(
        owner: str,
        repo: str,
        username: str,
        confirm: bool = False,
    ) -> dict:
        """Remove someone's access to a repository.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Removing {username} from {owner}/{repo} "
                        "requires explicit user confirmation."
                    ),
                },
            }

        result = await github.remove_collaborator(
            owner=owner,
            repo=repo,
            username=username,
        )

        return {"success": True, **result}

    # =================================================================
    # BRANCHES, COMMITS AND FILE CONTENTS
    # =================================================================

    @mcp.tool()
    @github_tool
    async def github_get_commit(
        owner: str,
        repo: str,
        ref: str,
    ) -> dict:
        """Get one commit, with its per-file change statistics.

        `ref` can be a commit SHA, a branch name or a tag.
        """

        commit = await github.get_commit(
            owner=owner,
            repo=repo,
            ref=ref,
        )

        return {
            "success": True,
            "commit": {
                "sha": commit.get("sha"),
                "message": commit.get("commit", {}).get("message"),
                "author": commit.get(
                    "commit", {}
                ).get("author", {}).get("name"),
                "date": commit.get(
                    "commit", {}
                ).get("author", {}).get("date"),
                "stats": commit.get("stats"),
                "url": commit.get("html_url"),
                "files": [
                    {
                        "filename": item.get("filename"),
                        "status": item.get("status"),
                        "additions": item.get("additions"),
                        "deletions": item.get("deletions"),
                    }
                    for item in commit.get("files", [])
                ],
            },
        }

    @mcp.tool()
    @github_tool
    async def github_compare_commits(
        owner: str,
        repo: str,
        base: str,
        head: str,
    ) -> dict:
        """Compare two branches, tags or commits.

        Answers "what is on `head` that is not on `base`?" - the same
        question a pull request asks. Use it to see what a merge would
        bring in before creating one.
        """

        result = await github.compare_commits(
            owner=owner,
            repo=repo,
            base=base,
            head=head,
        )

        return {
            "success": True,
            "status": result.get("status"),
            "ahead_by": result.get("ahead_by"),
            "behind_by": result.get("behind_by"),
            "total_commits": result.get("total_commits"),
            "url": result.get("html_url"),
            "files": [
                {
                    "filename": item.get("filename"),
                    "status": item.get("status"),
                    "additions": item.get("additions"),
                    "deletions": item.get("deletions"),
                }
                for item in result.get("files", [])
            ],
        }

    @mcp.tool()
    @github_tool
    async def github_list_tags(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List a repository's git tags."""

        tags = await github.list_tags(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "tags": [
                {
                    "name": tag.get("name"),
                    "sha": tag.get("commit", {}).get("sha"),
                }
                for tag in tags
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_get_readme(
        owner: str,
        repo: str,
        ref: str | None = None,
    ) -> dict:
        """Get a repository's README.

        Content comes back base64-encoded, exactly as GitHub sends it.
        """

        readme = await github.get_readme(
            owner=owner,
            repo=repo,
            ref=ref,
        )

        return {
            "success": True,
            "name": readme.get("name"),
            "path": readme.get("path"),
            "size": readme.get("size"),
            "encoding": readme.get("encoding"),
            "content": readme.get("content"),
            "html_url": readme.get("html_url"),
        }

    @mcp.tool()
    @github_tool
    async def github_create_branch(
        owner: str,
        repo: str,
        branch: str,
        from_branch: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Create a new branch in a GitHub repository.

        The branch is cut from `from_branch`, or from the repository's
        default branch when that is not given. You do not need to look
        up a commit SHA first - this resolves it.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Creating a branch requires explicit "
                        "user confirmation."
                    ),
                },
            }

        created = await github.create_branch(
            owner=owner,
            repo=repo,
            branch=branch,
            from_branch=from_branch,
        )

        return {
            "success": True,
            "branch": {
                "ref": created.get("ref"),
                "sha": created.get("object", {}).get("sha"),
                "created_from": created.get("created_from"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_delete_branch(
        owner: str,
        repo: str,
        branch: str,
        confirm: bool = False,
    ) -> dict:
        """Delete a branch from a GitHub repository.

        Unmerged commits on the branch become unreachable. Do not use
        this on a repository's default branch.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Deleting branch {branch!r} requires "
                        "explicit user confirmation."
                    ),
                },
            }

        result = await github.delete_branch(
            owner=owner,
            repo=repo,
            branch=branch,
        )

        return {"success": True, **result}

    @mcp.tool()
    @github_tool
    async def github_update_file(
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Create or replace a text file, as a commit.

        Pass ordinary text as `content` - it is base64-encoded for you.
        If the file already exists its whole content is REPLACED, not
        appended to; read it first if you mean to edit it.

        `branch` defaults to the repository's default branch, which is
        usually the one you should not be committing to directly.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Committing to {path!r} requires explicit "
                        "user confirmation."
                    ),
                },
            }

        result = await github.create_or_update_file(
            owner=owner,
            repo=repo,
            path=path,
            content=content,
            message=message,
            branch=branch,
            sha=sha,
        )

        commit = result.get("commit", {})
        entry = result.get("content") or {}

        return {
            "success": True,
            "commit": {
                "sha": commit.get("sha"),
                "message": commit.get("message"),
                "url": commit.get("html_url"),
            },
            "file": {
                "path": entry.get("path"),
                "sha": entry.get("sha"),
                "size": entry.get("size"),
                "html_url": entry.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_delete_file(
        owner: str,
        repo: str,
        path: str,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Delete a file from a repository, as a commit.

        The file stays in the repository's history, so this is
        recoverable - unlike deleting the repository itself.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Deleting {path!r} requires explicit "
                        "user confirmation."
                    ),
                },
            }

        result = await github.delete_file(
            owner=owner,
            repo=repo,
            path=path,
            message=message,
            branch=branch,
            sha=sha,
        )

        commit = result.get("commit", {})

        return {
            "success": True,
            "deleted_path": path,
            "commit": {
                "sha": commit.get("sha"),
                "message": commit.get("message"),
                "url": commit.get("html_url"),
            },
        }

    # =================================================================
    # ISSUE COMMENTS, LABELS AND MILESTONES
    # =================================================================

    @mcp.tool()
    @github_tool
    async def github_list_issue_comments(
        owner: str,
        repo: str,
        issue_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """Read the comments on a GitHub issue or pull request."""

        comments = await github.list_issue_comments(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "comments": [
                {
                    "id": comment.get("id"),
                    "author": comment.get("user", {}).get("login"),
                    "body": comment.get("body"),
                    "created_at": comment.get("created_at"),
                    "url": comment.get("html_url"),
                }
                for comment in comments
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_list_labels(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the labels defined in a repository."""

        labels = await github.list_labels(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "labels": [
                {
                    "name": label.get("name"),
                    "color": label.get("color"),
                    "description": label.get("description"),
                }
                for label in labels
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_create_label(
        owner: str,
        repo: str,
        name: str,
        color: str = "ededed",
        description: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Create a label in a repository.

        `color` is six hexadecimal digits without a leading '#',
        for example 'd73a4a' for red.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Creating a label requires explicit "
                        "user confirmation."
                    ),
                },
            }

        label = await github.create_label(
            owner=owner,
            repo=repo,
            name=name,
            color=color,
            description=description,
        )

        return {
            "success": True,
            "label": {
                "name": label.get("name"),
                "color": label.get("color"),
                "description": label.get("description"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_add_issue_labels(
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
        confirm: bool = False,
    ) -> dict:
        """Add labels to an issue or pull request.

        Labels are ADDED to whatever is already there; existing labels
        are not removed. The labels must already exist in the
        repository.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Labelling an issue requires explicit "
                        "user confirmation."
                    ),
                },
            }

        result = await github.add_issue_labels(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            labels=labels,
        )

        return {
            "success": True,
            "issue_number": issue_number,
            "labels": [item.get("name") for item in result],
        }

    @mcp.tool()
    @github_tool
    async def github_remove_issue_label(
        owner: str,
        repo: str,
        issue_number: int,
        label: str,
        confirm: bool = False,
    ) -> dict:
        """Remove one label from an issue or pull request.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Removing a label requires explicit "
                        "user confirmation."
                    ),
                },
            }

        result = await github.remove_issue_label(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            label=label,
        )

        return {"success": True, **result}

    @mcp.tool()
    @github_tool
    async def github_list_milestones(
        owner: str,
        repo: str,
        state: str = "open",
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List a repository's milestones."""

        milestones = await github.list_milestones(
            owner=owner,
            repo=repo,
            state=state,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "milestones": [
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "due_on": item.get("due_on"),
                    "open_issues": item.get("open_issues"),
                    "closed_issues": item.get("closed_issues"),
                    "url": item.get("html_url"),
                }
                for item in milestones
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_list_assignees(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the users who can be assigned to issues in a repository."""

        assignees = await github.list_assignees(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "assignees": [
                {
                    "login": person.get("login"),
                    "url": person.get("html_url"),
                }
                for person in assignees
            ],
            "page": page,
            "per_page": per_page,
        }

    # =================================================================
    # PULL REQUEST REVIEW AND MERGE
    # =================================================================

    @mcp.tool()
    @github_tool
    async def github_update_pull_request(
        owner: str,
        repo: str,
        pull_number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        base: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Update a pull request, or close it.

        Pass state='closed' to close a pull request without merging it.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Updating a pull request requires explicit "
                        "user confirmation."
                    ),
                },
            }

        pull = await github.update_pull_request(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            title=title,
            body=body,
            state=state,
            base=base,
        )

        return {
            "success": True,
            "pull_request": {
                "number": pull.get("number"),
                "title": pull.get("title"),
                "state": pull.get("state"),
                "base": pull.get("base", {}).get("ref"),
                "url": pull.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_merge_pull_request(
        owner: str,
        repo: str,
        pull_number: int,
        merge_method: str = "merge",
        commit_title: str | None = None,
        commit_message: str | None = None,
        sha: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Merge a pull request into its base branch.

        This puts somebody's code into a shared branch, which usually
        triggers deployments. `merge_method` is 'merge', 'squash' or
        'rebase'.

        Pass `sha` (the head commit you reviewed) to have GitHub refuse
        the merge if anyone has pushed since - worth doing whenever a
        human approved a specific diff.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Merging pull request #{pull_number} requires "
                        "explicit user confirmation."
                    ),
                },
            }

        result = await github.merge_pull_request(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            merge_method=merge_method,
            commit_title=commit_title,
            commit_message=commit_message,
            sha=sha,
        )

        return {
            "success": True,
            "merged": result.get("merged"),
            "sha": result.get("sha"),
            "message": result.get("message"),
        }

    @mcp.tool()
    @github_tool
    async def github_list_pull_request_files(
        owner: str,
        repo: str,
        pull_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the files a pull request changes, with their diffs."""

        files = await github.list_pull_request_files(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "files": [
                {
                    "filename": item.get("filename"),
                    "status": item.get("status"),
                    "additions": item.get("additions"),
                    "deletions": item.get("deletions"),
                    "changes": item.get("changes"),
                    "patch": item.get("patch"),
                }
                for item in files
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_list_pull_request_commits(
        owner: str,
        repo: str,
        pull_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the commits contained in a pull request."""

        commits = await github.list_pull_request_commits(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "commits": [
                {
                    "sha": commit.get("sha"),
                    "message": commit.get("commit", {}).get("message"),
                    "author": commit.get(
                        "commit", {}
                    ).get("author", {}).get("name"),
                    "date": commit.get(
                        "commit", {}
                    ).get("author", {}).get("date"),
                    "url": commit.get("html_url"),
                }
                for commit in commits
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_list_pull_request_reviews(
        owner: str,
        repo: str,
        pull_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the reviews left on a pull request."""

        reviews = await github.list_pull_request_reviews(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "reviews": [
                {
                    "id": review.get("id"),
                    "reviewer": review.get("user", {}).get("login"),
                    "state": review.get("state"),
                    "body": review.get("body"),
                    "submitted_at": review.get("submitted_at"),
                    "url": review.get("html_url"),
                }
                for review in reviews
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_create_pull_request_review(
        owner: str,
        repo: str,
        pull_number: int,
        event: str = "COMMENT",
        body: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """Review a pull request: COMMENT, APPROVE or REQUEST_CHANGES.

        An APPROVE review is a human signature on code - on a protected
        branch it is what allows the merge to happen. COMMENT and
        REQUEST_CHANGES both require a body explaining them.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        f"Submitting a {event} review requires "
                        "explicit user confirmation."
                    ),
                },
            }

        review = await github.create_pull_request_review(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            event=event,
            body=body,
        )

        return {
            "success": True,
            "review": {
                "id": review.get("id"),
                "state": review.get("state"),
                "url": review.get("html_url"),
            },
        }

    # =================================================================
    # RELEASES, ACTIONS, GISTS, STARS AND NOTIFICATIONS
    # =================================================================

    @mcp.tool()
    @github_tool
    async def github_list_releases(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List a repository's releases."""

        releases = await github.list_releases(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "releases": [
                {
                    "id": release.get("id"),
                    "tag_name": release.get("tag_name"),
                    "name": release.get("name"),
                    "draft": release.get("draft"),
                    "prerelease": release.get("prerelease"),
                    "published_at": release.get("published_at"),
                    "url": release.get("html_url"),
                }
                for release in releases
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_get_latest_release(
        owner: str,
        repo: str,
    ) -> dict:
        """Get a repository's most recent published release.

        Drafts and prereleases are skipped - this is the version a user
        would actually download.
        """

        release = await github.get_latest_release(
            owner=owner,
            repo=repo,
        )

        return {
            "success": True,
            "release": {
                "id": release.get("id"),
                "tag_name": release.get("tag_name"),
                "name": release.get("name"),
                "body": release.get("body"),
                "published_at": release.get("published_at"),
                "url": release.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_get_release(
        owner: str,
        repo: str,
        release_id: int,
    ) -> dict:
        """Get one release by its numeric id."""

        release = await github.get_release(
            owner=owner,
            repo=repo,
            release_id=release_id,
        )

        return {
            "success": True,
            "release": {
                "id": release.get("id"),
                "tag_name": release.get("tag_name"),
                "name": release.get("name"),
                "body": release.get("body"),
                "draft": release.get("draft"),
                "prerelease": release.get("prerelease"),
                "published_at": release.get("published_at"),
                "url": release.get("html_url"),
                "assets": [
                    {
                        "name": asset.get("name"),
                        "size": asset.get("size"),
                        "download_count": asset.get("download_count"),
                        "url": asset.get("browser_download_url"),
                    }
                    for asset in release.get("assets", [])
                ],
            },
        }

    @mcp.tool()
    @github_tool
    async def github_create_release(
        owner: str,
        repo: str,
        tag_name: str,
        name: str | None = None,
        body: str | None = None,
        draft: bool = False,
        prerelease: bool = False,
        target_commitish: str | None = None,
        generate_release_notes: bool = False,
        confirm: bool = False,
    ) -> dict:
        """Publish a release for a repository.

        Publishing notifies watchers and, for a package repository, is
        what makes a version installable by other people. Use
        draft=True to prepare one without publishing it.

        Requires explicit user confirmation.
        """

        if not confirm:
            return {
                "success": False,
                "error": {
                    "type": "ConfirmationRequired",
                    "message": (
                        "Publishing a release requires explicit "
                        "user confirmation."
                    ),
                },
            }

        release = await github.create_release(
            owner=owner,
            repo=repo,
            tag_name=tag_name,
            name=name,
            body=body,
            draft=draft,
            prerelease=prerelease,
            target_commitish=target_commitish,
            generate_release_notes=generate_release_notes,
        )

        return {
            "success": True,
            "release": {
                "id": release.get("id"),
                "tag_name": release.get("tag_name"),
                "name": release.get("name"),
                "draft": release.get("draft"),
                "prerelease": release.get("prerelease"),
                "url": release.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_list_workflows(
        owner: str,
        repo: str,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the GitHub Actions workflows defined in a repository."""

        data = await github.list_workflows(
            owner=owner,
            repo=repo,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "total_count": data.get("total_count"),
            "workflows": [
                {
                    "id": workflow.get("id"),
                    "name": workflow.get("name"),
                    "path": workflow.get("path"),
                    "state": workflow.get("state"),
                    "url": workflow.get("html_url"),
                }
                for workflow in data.get("workflows", [])
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_list_workflow_runs(
        owner: str,
        repo: str,
        workflow_id: str | None = None,
        branch: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List GitHub Actions runs - i.e. whether CI is passing.

        Without `workflow_id` this returns runs of every workflow in
        the repository. `workflow_id` accepts either the numeric id or
        the file name, e.g. 'ci.yml'. `status` filters on values such
        as 'completed', 'in_progress', 'failure' or 'success'.
        """

        data = await github.list_workflow_runs(
            owner=owner,
            repo=repo,
            workflow_id=workflow_id,
            branch=branch,
            status=status,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "total_count": data.get("total_count"),
            "runs": [
                {
                    "id": run.get("id"),
                    "name": run.get("name"),
                    "branch": run.get("head_branch"),
                    "event": run.get("event"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "created_at": run.get("created_at"),
                    "url": run.get("html_url"),
                }
                for run in data.get("workflow_runs", [])
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_get_workflow_run(
        owner: str,
        repo: str,
        run_id: int,
    ) -> dict:
        """Get one GitHub Actions run, including why it finished."""

        run = await github.get_workflow_run(
            owner=owner,
            repo=repo,
            run_id=run_id,
        )

        return {
            "success": True,
            "run": {
                "id": run.get("id"),
                "name": run.get("name"),
                "branch": run.get("head_branch"),
                "commit_sha": run.get("head_sha"),
                "event": run.get("event"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "run_attempt": run.get("run_attempt"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "url": run.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_list_gists(
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List your GitHub gists."""

        gists = await github.list_gists(
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "gists": [
                {
                    "id": gist.get("id"),
                    "description": gist.get("description"),
                    "public": gist.get("public"),
                    "files": list(gist.get("files", {}).keys()),
                    "updated_at": gist.get("updated_at"),
                    "url": gist.get("html_url"),
                }
                for gist in gists
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_create_gist(
        files: dict,
        description: str | None = None,
        public: bool = False,
    ) -> dict:
        """Create a gist from a mapping of {filename: content}.

        Gists are SECRET by default here. A public gist is permanently
        and publicly readable, and search engines index them, so pass
        public=True only when the user asked for that.
        """

        gist = await github.create_gist(
            files={
                name: str(content)
                for name, content in (files or {}).items()
            },
            description=description,
            public=public,
        )

        return {
            "success": True,
            "gist": {
                "id": gist.get("id"),
                "description": gist.get("description"),
                "public": gist.get("public"),
                "files": list(gist.get("files", {}).keys()),
                "url": gist.get("html_url"),
            },
        }

    @mcp.tool()
    @github_tool
    async def github_list_starred_repositories(
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List the repositories you have starred."""

        repositories = await github.list_starred_repositories(
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "repositories": [
                {
                    "full_name": repo.get("full_name"),
                    "description": repo.get("description"),
                    "language": repo.get("language"),
                    "stars": repo.get("stargazers_count"),
                    "url": repo.get("html_url"),
                }
                for repo in repositories
            ],
            "page": page,
            "per_page": per_page,
        }

    @mcp.tool()
    @github_tool
    async def github_star_repository(
        owner: str,
        repo: str,
    ) -> dict:
        """Star a GitHub repository.

        Starring is public - other people can see what you have
        starred - but it changes nothing and is trivially undone.
        """

        result = await github.star_repository(owner=owner, repo=repo)

        return {"success": True, **result}

    @mcp.tool()
    @github_tool
    async def github_unstar_repository(
        owner: str,
        repo: str,
    ) -> dict:
        """Remove your star from a GitHub repository."""

        result = await github.unstar_repository(owner=owner, repo=repo)

        return {"success": True, **result}

    @mcp.tool()
    @github_tool
    async def github_list_notifications(
        all: bool = False,
        participating: bool = False,
        page: int = 1,
        per_page: int = 30,
    ) -> dict:
        """List your GitHub notifications.

        By default only UNREAD notifications are returned. Pass
        all=True to include ones already read, or participating=True
        to see only threads you are directly involved in.
        """

        notifications = await github.list_notifications(
            all=all,
            participating=participating,
            page=page,
            per_page=per_page,
        )

        return {
            "success": True,
            "notifications": [
                {
                    "id": item.get("id"),
                    "reason": item.get("reason"),
                    "unread": item.get("unread"),
                    "title": item.get("subject", {}).get("title"),
                    "type": item.get("subject", {}).get("type"),
                    "repository": item.get(
                        "repository", {}
                    ).get("full_name"),
                    "updated_at": item.get("updated_at"),
                }
                for item in notifications
            ],
            "page": page,
            "per_page": per_page,
        }
