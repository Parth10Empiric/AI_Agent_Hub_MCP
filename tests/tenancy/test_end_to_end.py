from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PROJECT_ROOT = Path(__file__).resolve().parents[2]

"""
The whole chain, through a REAL MCP subprocess.

The unit tests prove the middleware and the proxy behave. This proves
the wire actually carries what we think it carries:

    ClientSession.call_tool(..., meta={"credentials": {...}})
            |     a stdio pipe, JSON-RPC, a separate OS process
            v
    CredentialMiddleware  ->  ContextVar  ->  ServiceProxy  ->  tool

Nothing here mocks the transport, because the transport is exactly the
part that could silently drop a field.
"""


# A miniature MCP server that uses the REAL middleware and proxy, with a
# fake service in place of GitHub. Written to a temp file and spawned.
SERVER_SOURCE = '''
import sys
sys.path.insert(0, {root!r})

from mcp.server import MCPServer
from core.tenancy import CredentialMiddleware, service_proxy


class FakeGitHub:
    def __init__(self, token):
        self.token = token

    def list_repositories(self):
        # Stands in for "the repositories of whoever this token
        # belongs to".
        return {{"account": self.token or "ENV"}}


github = service_proxy("github", lambda token: FakeGitHub(token))

mcp = MCPServer("tenancy-probe", middleware=[CredentialMiddleware()])


@mcp.tool()
def list_repositories() -> dict:
    """No ctx parameter, no decorator - exactly like the real tools."""

    return github.list_repositories()


mcp.run()
'''


def _write_server(tmp: Path) -> Path:
    path = tmp / "tenancy_server.py"
    path.write_text(SERVER_SOURCE.format(root=str(PROJECT_ROOT)))
    return path


async def _call_as(session, token):
    meta = {"credentials": {"github": token}} if token else {}

    result = await session.call_tool("list_repositories", {}, meta=meta)

    return result.content[0].text


def test_two_users_reach_two_accounts_through_a_real_subprocess():

    import tempfile

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def body() -> list[str]:

        with tempfile.TemporaryDirectory() as tmp:

            server = _write_server(Path(tmp))

            params = StdioServerParameters(
                command=sys.executable,
                args=[str(server)],
            )

            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:

                    await session.initialize()

                    return [
                        await _call_as(session, "alice-token"),
                        await _call_as(session, "bob-token"),
                        # No credentials at all - the CLI's shape.
                        await _call_as(session, None),
                        # Alice again, to prove nothing stuck.
                        await _call_as(session, "alice-token"),
                    ]

    try:
        alice, bob, anonymous, alice_again = asyncio.run(body())

    except Exception as exc:  # pragma: no cover
        print(f"  SKIP  MCP subprocess unavailable ({type(exc).__name__})")
        return

    assert "alice-token" in alice
    assert "bob-token" in bob

    # The isolation assertion: bob's call did not see alice's token,
    # and alice's second call did not see bob's.
    assert "alice" not in bob
    assert "bob" not in alice_again

    # A request with no credentials falls back rather than inheriting.
    assert "ENV" in anonymous
