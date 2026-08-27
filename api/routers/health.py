from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text

from core.logging import get_logger

from api.settings import APISettings, get_settings


logger = get_logger(__name__)


# A router is a group of routes that can be attached to an app later.
#
# Routes are NOT declared on the app directly, because with the app
# factory pattern the app does not exist at import time - it is built
# inside create_app(). Routers do exist at import time, so this is how
# routes get split across files.
#
# `tags` groups these endpoints together in the /docs page.
router = APIRouter(tags=["health"])


# FastAPI's dependency injection, in one line.
#
# Reads as: "this parameter is an APISettings, and FastAPI should
# obtain it by calling get_settings()".
#
# The alias exists so every endpoint needing settings writes
# `settings: SettingsDep` instead of repeating the whole expression.
SettingsDep = Annotated[APISettings, Depends(get_settings)]


@router.get("/health")
async def health(settings: SettingsDep) -> dict[str, str]:
    """
    Liveness check: is this process up and configured?

    Deliberately NOT under the /api prefix. This is called by Docker,
    a load balancer or an uptime monitor - infrastructure, not the
    frontend - and those callers should not have to care how the
    application namespaces its routes.

    Deliberately does NOT touch the database. "Is the process alive?"
    and "are its dependencies healthy?" are different questions with
    different responses: a dead process must be restarted, a dead
    database must not. A readiness check comes later, separately.
    """

    return {
        "status": "ok",
        "version": settings.api_version,
        "environment": settings.api_environment,
    }


# How long ONE dependency check may take.
#
# A readiness probe that can hang is worse than no readiness probe.
# Without this timeout, a database that accepts the TCP connection and
# then never answers - a failing-over primary, a saturated pool, a
# firewall dropping established connections - makes /ready hang too.
# The load balancer's own probe then times out, which it cannot
# distinguish from "slow but fine", so it leaves the container in
# rotation while every request piles up behind the same dead pool.
#
# Two seconds is longer than a healthy SELECT 1 (single-digit
# milliseconds) by three orders of magnitude, and short enough to fit
# inside any sane probe interval.
CHECK_TIMEOUT_SECONDS = 2.0


async def _guarded(name: str, check) -> tuple[str, str]:
    """
    Run one dependency check so that it CANNOT take the endpoint down.

    Every failure mode collapses to a short string:

        ok                      the dependency answered
        timeout after 2.0s      it accepted the call and went quiet
        unavailable (OSError)   it refused, or is not wired up yet

    WHY THE EXCEPTION TEXT IS THROWN AWAY

    Only the exception's TYPE is reported. A database error message
    routinely contains the DSN it failed to connect with - and the DSN
    contains the password. /ready is unauthenticated by necessity, so
    anything it returns is public. The full detail goes to the log,
    where it is already protected.
    """

    try:
        await asyncio.wait_for(check, timeout=CHECK_TIMEOUT_SECONDS)

        return name, "ok"

    except asyncio.TimeoutError:
        logger.warning("readiness: %s timed out", name)

        return name, f"timeout after {CHECK_TIMEOUT_SECONDS}s"

    except Exception as exc:
        # exc_info, so the real cause is recoverable from the logs -
        # which is the whole reason it is safe to hide it above.
        logger.warning("readiness: %s failed", name, exc_info=exc)

        return name, f"unavailable ({type(exc).__name__})"


async def _check_database(engine) -> None:
    """
    Take a connection from the pool and make the server answer.

    `SELECT 1` rather than merely opening a connection, because
    pool_pre_ping already proves a socket exists. What this proves is
    that PostgreSQL is still executing statements - a database that has
    exhausted max_connections, or is stuck in recovery, will accept the
    socket and refuse to work.
    """

    if engine is None:
        raise RuntimeError("database engine is not configured")

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_mcp(provider) -> None:
    """
    Is the MCP subprocess alive and past its handshake?

    Cheap on purpose: a property read, not a tool call. Listing tools
    here would take the provider's lock, so a readiness probe every
    five seconds would contend with real traffic for the one serialised
    resource in the process.
    """

    if provider is None:
        raise RuntimeError("MCP provider is not configured")

    if not provider.healthy:
        raise RuntimeError("MCP session is not established")


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
    """
    Readiness check: can this process serve a REAL request right now?

    THIS IS NOT /health, AND THE DIFFERENCE IS OPERATIONAL

        /health fails  -> the orchestrator RESTARTS the container
        /ready  fails  -> the load balancer STOPS SENDING TRAFFIC,
                          and the container keeps running

    Those are opposite responses, which is why they are opposite
    endpoints. Put a database check inside /health and a two-second
    PostgreSQL blip restarts every container at once - and the restart
    storm then hammers the database that was just recovering. A minor
    blip becomes a total outage, caused entirely by the monitoring.

    So this endpoint reports dependencies and /health never will.

    WHY 503 AND NOT 500

    503 means "temporarily unable, come back". Load balancers and
    orchestrators understand it as a retryable state and take the
    instance out of rotation without killing it. A 500 says "this
    request was broken", which is a claim about the request, not the
    server, and nothing retries it.

    Checks run CONCURRENTLY: a probe should cost the slowest
    dependency, not their sum.
    """

    # getattr rather than attribute access: app.state is populated by
    # the lifespan handler, and a test that builds an app without
    # running lifespan should get a clean "unavailable" here rather
    # than an AttributeError that reads like a bug in the endpoint.
    engine = getattr(request.app.state, "db_engine", None)
    provider = getattr(request.app.state, "mcp_provider", None)

    results = await asyncio.gather(
        _guarded("database", _check_database(engine)),
        _guarded("mcp", _check_mcp(provider)),
    )

    checks = dict(results)

    ok = all(value == "ok" for value in checks.values())

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ok else "not ready",
        "checks": checks,
    }
