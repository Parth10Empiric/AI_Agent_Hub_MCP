"""
The MCP transport layer.

Phase 2 talks to the MCP server through a `ClientSession` object. The
CLI created exactly one and held it for the life of the process. A web
server cannot do that naively, and cannot create one per request
either:

    100 concurrent users
          -> 100 python subprocesses
          -> 100 x (process spawn + MCP handshake + OAuth)
          -> the server falls over

So the session lives behind an interface (`MCPSessionProvider`), and
how it is actually obtained is an implementation detail that can change
without touching a single endpoint.

    A  SharedSessionProvider   one session, serialised by a lock
    B  a small pool            real concurrency, bounded resources
    C  HTTP/SSE transport      horizontally scalable, the end state

We ship A and design for C. Same move as EmbeddingProvider and
PermissionPolicy in Phase 2: pick the simple implementation, but put
the seam in on day one so the swap is one class, not a rewrite.
"""
