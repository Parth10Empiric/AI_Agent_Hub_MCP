from __future__ import annotations

import os
from dataclasses import dataclass

from core.errors import ConfigurationError

from api.settings import APISettings


"""
Refuse to start with a broken configuration (Phase 6.1).

WHY A SERVER THAT WILL NOT BOOT IS BETTER THAN ONE THAT WILL

A misconfigured process that starts and then fails on the first real
request LOOKS HEALTHY to whatever is watching it. /health answers 200,
the orchestrator marks the container up, the load balancer sends it
traffic, and every one of those requests fails. A process that refuses
to start is taken out of rotation automatically and the previous
version keeps serving.

So the goal here is to turn "quietly wrong" into "loudly absent".

WHY THIS MODULE EXISTS RATHER THAN A SECOND COPY OF THE CHECKS

scripts/check_production.py has performed these checks since Phase 5.
Startup validation could have re-implemented them, and that is exactly
the mistake: two copies of a security check drift, and the copy that
drifts is always the one nobody runs. There is now ONE list of checks
and two callers -

    scripts/check_production.py   prints them, exits 1 on a problem
    api.main.create_app          raises on a problem, in production

- so a check added here is enforced in both places for free.

WHAT DELIBERATELY IS NOT HERE

Anything that performs I/O. Every function below is pure and
synchronous, for two reasons:

  1. create_app() is synchronous, and a network call inside it would
     have to invent an event loop.

  2. "Can I reach the database?" is a DIFFERENT question, with a
     different answer over time, and it already has an endpoint:
     /ready. Configuration is fixed at boot; dependencies are not.

The async audit-role query stays in scripts/check_production.py, which
is an operator tool and may take its time.
"""


@dataclass(frozen=True)
class Check:
    """
    One verdict.

    `problem` and `warning` are separate fields rather than a severity
    enum because the SAME condition is often both, depending on the
    environment: rate limiting switched off is a note in development
    and a refusal to boot in production. The check decides which field
    to populate; the caller does not have to know the rule.
    """

    name: str
    ok: bool
    detail: str = ""

    # Populated only when this must BLOCK. Anything in here stops
    # startup in production.
    problem: str | None = None

    # Advisory. Printed, logged, never fatal.
    warning: str | None = None


def _env_credentials_enabled() -> bool:
    """
    Would an anonymous MCP call fall back to the tokens in .env?

    Read from the environment rather than APISettings because this is
    the MCP SERVER's setting (config/settings.py), and the two config
    objects are deliberately separate - server.py runs as a subprocess
    and has no business knowing the JWT signing key.
    """

    override = os.getenv("MCP_ALLOW_ENV_CREDENTIALS")

    if override is not None:
        return override.strip().lower() in {"1", "true", "yes"}

    return os.getenv("MCP_ENVIRONMENT", "development").strip().lower() != "production"


def run_checks(settings: APISettings) -> list[Check]:
    """
    Every synchronous configuration check, in order of severity.

    Returns verdicts rather than printing or raising, so the operator
    script can format them and the application can enforce them.
    """

    production = settings.is_production

    checks: list[Check] = []

    # --- 5.5 the one that leaks accounts -----------------------------
    #
    # FIRST because it is the worst failure on the list. Every other
    # problem here degrades the service; this one hands user A the
    # operator's GitHub account.
    mcp_env = os.getenv("MCP_ENVIRONMENT", "development")
    env_creds = _env_credentials_enabled()

    checks.append(
        Check(
            name="5.5 per-user credentials",
            ok=not (production and env_creds),
            detail=mcp_env,
            problem=(
                "MCP_ENVIRONMENT is not 'production' (or "
                "MCP_ALLOW_ENV_CREDENTIALS is on), so a user who has "
                "not connected a service gets YOUR tokens from .env. "
                "This is a cross-tenant leak."
                if production and env_creds
                else None
            ),
        )
    )

    # --- 5.4 encryption ----------------------------------------------
    try:
        keys = settings.encryption_keys

        if not keys or not keys[0]:
            checks.append(
                Check(
                    name="5.4 credential encryption",
                    ok=False,
                    problem="CREDENTIAL_ENCRYPTION_KEY is empty.",
                )
            )
        else:
            checks.append(
                Check(
                    name="5.4 credential encryption",
                    ok=True,
                    detail=f"{len(keys)} key(s)",
                    # More than one key means a rotation is HALF DONE.
                    # Safe to run, unsafe to forget: step 4 of the
                    # rotation drops the old key, and any row still
                    # encrypted with it becomes unreadable forever.
                    warning=(
                        f"{len(keys)} keys are configured, which means "
                        "a rotation is in progress. Finish it with "
                        "scripts/rotate_credentials.py --apply, then "
                        "drop the old key."
                        if len(keys) > 1
                        else None
                    ),
                )
            )

    except Exception as exc:
        checks.append(
            Check(
                name="5.4 credential encryption",
                ok=False,
                problem=f"credential store will not build: {exc}",
            )
        )

    # --- 5.7 rate limits ---------------------------------------------
    if not settings.rate_limit_enabled:
        message = (
            "Rate limiting is switched off. Nothing bounds cost or "
            "blast radius - one loop is a bill, and one compromised "
            "agent has all hour."
        )
        checks.append(
            Check(
                name="5.7 rate limiting",
                ok=False,
                detail="disabled",
                problem=message if production else None,
                warning=None if production else message,
            )
        )
    else:
        checks.append(
            Check(
                name="5.7 rate limiting",
                ok=True,
                detail=(
                    f"{settings.agent_turns_per_hour} turns/h, "
                    f"{settings.dangerous_ops_per_hour} high-risk/h"
                ),
            )
        )

    # --- 5.2 approvals -----------------------------------------------
    positive = settings.approval_timeout_seconds > 0

    checks.append(
        Check(
            name="5.2 approvals",
            ok=positive,
            detail=f"{settings.approval_timeout_seconds}s timeout",
            problem=(
                None
                if positive
                else "APPROVAL_TIMEOUT_SECONDS must be positive. An "
                "approval that waits forever leaks a request slot."
            ),
        )
    )

    # --- 5.3 OAuth ----------------------------------------------------
    #
    # Imported here rather than at module scope: api.oauth pulls in the
    # provider registry, and preflight is imported by create_app before
    # anything else. A local import keeps the startup path free of an
    # import cycle that would only appear once someone reorders a file.
    from api.oauth import supports_oauth

    configured = [
        key
        for key in ("github", "google_drive", "google_calendar", "slack")
        if supports_oauth(settings, key)
    ]

    checks.append(
        Check(
            name="5.3 OAuth",
            ok=True,
            detail=f"{len(configured)} provider(s): {', '.join(configured) or 'none'}",
            warning=(
                "No OAuth provider is configured, so users can only "
                "connect services by pasting a personal access token."
                if production and not configured
                else None
            ),
        )
    )

    # --- transport ----------------------------------------------------
    #
    # Two separate URLs, two separate reasons, so two separate checks.
    redirect_insecure = production and settings.oauth_redirect_base.startswith("http://")

    checks.append(
        Check(
            name="6.1 OAuth redirect over TLS",
            ok=not redirect_insecure,
            detail=settings.oauth_redirect_base,
            problem=(
                "OAUTH_REDIRECT_BASE is http://. An authorization code "
                "sent over plaintext is an authorization code anyone "
                "on the path can use."
                if redirect_insecure
                else None
            ),
        )
    )

    frontend_insecure = production and not settings.frontend_base_url.startswith(
        "https://"
    )

    checks.append(
        Check(
            name="6.1 frontend over TLS",
            ok=not frontend_insecure,
            detail=settings.frontend_base_url,
            problem=(
                "FRONTEND_BASE_URL is not https. The refresh-token "
                "cookie needs Secure, and Secure needs TLS."
                if frontend_insecure
                else None
            ),
        )
    )

    # --- 6.1 logging --------------------------------------------------
    #
    # DEBUG is not merely noisy. SQLAlchemy at DEBUG prints bound
    # parameters, which is every password hash and every token that
    # passes through a query, into a file with far weaker access
    # control than the database it came from.
    log_level = os.getenv("MCP_LOG_LEVEL", "INFO").strip().upper()
    debug_logging = production and log_level == "DEBUG"

    checks.append(
        Check(
            name="6.1 log level",
            ok=not debug_logging,
            detail=log_level,
            problem=(
                "MCP_LOG_LEVEL is DEBUG. Debug logging prints bound "
                "query parameters and request bodies - credentials end "
                "up in the log file."
                if debug_logging
                else None
            ),
        )
    )

    # --- the single-worker constraint ---------------------------------
    #
    # Always a warning, never a problem: it is a uvicorn command-line
    # flag and nothing in this process can read it. It matters twice -
    # api/notifier.py and api/ratelimit.py both keep state in ONE
    # process's memory. Phase 6.4 removes this note by removing the
    # constraint.
    checks.append(
        Check(
            name="6.4 single worker",
            ok=True,
            detail="not verifiable from inside the process",
            warning=(
                "Run ONE uvicorn worker until Redis (Phase 6.4). With "
                "N workers the rate limits become N times larger and "
                "approvals resolved on the wrong worker time out."
            ),
        )
    )

    return checks


def problems(checks: list[Check]) -> list[str]:
    return [check.problem for check in checks if check.problem]


def advisories(checks: list[Check]) -> list[str]:
    return [check.warning for check in checks if check.warning]


def enforce(settings: APISettings) -> list[Check]:
    """
    Validate, and in production refuse to continue on a problem.

    Called from create_app() BEFORE the FastAPI instance exists, so a
    misconfigured deployment never reaches the point of binding a port.
    Raising from the lifespan handler would be too late: by then uvicorn
    has already opened the socket, and some orchestrators treat an open
    socket as "up".

    OUTSIDE PRODUCTION THIS ONLY WARNS, ON PURPOSE.

    A validator that blocks local development gets commented out within
    a week, and a commented-out validator protects production exactly
    as much as no validator at all. Development stays loose; the strict
    reading is what `API_ENVIRONMENT=production` buys.
    """

    checks = run_checks(settings)

    found = problems(checks)

    if found and settings.is_production:
        raise ConfigurationError(
            "Refusing to start: "
            + str(len(found))
            + " configuration problem(s).\n\n"
            + "\n".join(f"  - {problem}" for problem in found)
        )

    return checks
