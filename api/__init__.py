"""
Agent Hub HTTP API (Phase 3).

This package wraps the Phase 2 Agent Engine in a multi-user web
service. The dependency arrow points ONE way:

    api/  ->  agent/      allowed
    agent/  ->  api/      never

`agent/` has 147 tests that run with no database, no network and no
web server. Importing api/ from agent/ would end that.
"""