import asyncio

from services.github.service import GitHubService


async def main():
    github = GitHubService()

    try:
        user = await github.get_authenticated_user()

        print("\nAuthenticated GitHub user:")
        print("Login:", user.get("login"))
        print("Name:", user.get("name"))
        print("Public repos:", user.get("public_repos"))

        repositories = await github.list_repositories(5)

        print("\nRepositories:")

        for repo in repositories:
            print(
                f"- {repo['name']} "
                f"({repo['full_name']})"
            )

    finally:
        await github.close()
        

if __name__ == "__main__":
    asyncio.run(main())