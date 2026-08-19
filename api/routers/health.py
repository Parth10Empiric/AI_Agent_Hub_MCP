from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.settings import APISettings, get_settings


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
