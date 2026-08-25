from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from agent.engine import AgentEngine

from api.credentials import CredentialStore
from api.db.models import User
from api.db.session import get_db
from api.notifier import ApprovalNotifier
from api.ratelimit import RateLimiter
from api.security import TokenError, decode_access_token
from api.settings import APISettings, get_settings


# Shared dependencies.
#
# Everything here is something more than one router needs. A dependency
# used by exactly one router belongs in that router.


SettingsDep = Annotated[APISettings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def get_agent_engine(request: Request) -> AgentEngine:
    """
    The one AgentEngine, built during startup.

    Read from app.state rather than constructed here: discovery spawns
    a subprocess and classifies 161 tools, which must happen once per
    process, not once per request.
    """

    engine: AgentEngine | None = getattr(request.app.state, "agent_engine", None)

    if engine is None:
        # Reached only if startup failed but the app kept serving.
        # 503, not 500: the request was fine, the dependency is not,
        # and retrying later may well work.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool registry is not available.",
        )

    return engine


def get_credential_store(request: Request) -> CredentialStore:
    """The Fernet store, built once during startup."""

    store: CredentialStore | None = getattr(
        request.app.state, "credential_store", None
    )

    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential store is not available.",
        )

    return store


def get_approval_notifier(request: Request) -> ApprovalNotifier:
    """
    The one ApprovalNotifier, built during startup.

    Must be the SAME object for the turn that waits and the request
    that resolves - it is a dict of asyncio.Events in this process's
    memory. Built per request, every approval would wait out its full
    timeout and deny, with nothing in the logs to say why.
    """

    notifier: ApprovalNotifier | None = getattr(
        request.app.state, "approval_notifier", None
    )

    if notifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Approvals are not available.",
        )

    return notifier


def get_rate_limiter(request: Request) -> RateLimiter:
    """
    The one RateLimiter, built during startup.

    Must be the SAME object for every request, or each one gets a fresh
    empty window and nothing is ever limited - a failure that looks
    exactly like everything working.
    """

    limiter: RateLimiter | None = getattr(
        request.app.state, "rate_limiter", None
    )

    if limiter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiting is not available.",
        )

    return limiter


def get_sessionmaker(request: Request):
    """
    The session FACTORY, for writes that must not join the request's
    transaction.

    Observational audit rows use this: a failed login rolls nothing
    back, but the record of the attempt must survive whatever the
    caller does next - and must never be able to poison it.
    """

    return getattr(request.app.state, "sessionmaker", None)


AgentEngineDep = Annotated[AgentEngine, Depends(get_agent_engine)]
SessionMakerDep = Annotated[object, Depends(get_sessionmaker)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
ApprovalNotifierDep = Annotated[
    ApprovalNotifier,
    Depends(get_approval_notifier),
]
CredentialStoreDep = Annotated[CredentialStore, Depends(get_credential_store)]


# auto_error=False so a MISSING header reaches our code instead of
# FastAPI raising its own 403 first.
#
# The default would answer 403 for "no credentials", which is wrong:
# 403 means "I know who you are and the answer is no". No credentials
# is 401.
_bearer = HTTPBearer(auto_error=False)


CredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(_bearer),
]


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    """
    401 with the WWW-Authenticate header the HTTP spec requires.

    Clients use that header to decide how to retry. Omitting it is a
    small correctness bug that some HTTP libraries genuinely notice.
    """

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: CredentialsDep,
    session: DbDep,
    settings: SettingsDep,
) -> User:
    """
    Resolve the caller from the Authorization header.

    Every protected endpoint depends on this, directly or indirectly.

        Authorization: Bearer <token>
              |
              v
        decode, with the algorithm PINNED
              |
              v
        type == "access"?            (a refresh token must not work)
              |
              v
        load the user
              |
              v
        is_active?
              |
              v
        hand the User to the endpoint

    Every failure is 401, never 403:

        401  I do not know who you are      -> log in and retry
        403  I know who you are, and no     -> do not retry

    The detail strings differ only where that leaks nothing. Whether a
    token expired or was forged is not the caller's business.
    """

    if credentials is None:
        raise _unauthorized()

    try:
        user_id = decode_access_token(credentials.credentials, settings)

    except TokenError:
        raise _unauthorized("Invalid or expired token") from None

    user = await session.get(User, user_id)

    # A valid signature over a user that no longer exists. The token
    # outlived the account - deleted, or signed by a key from a
    # previous database.
    if user is None or not user.is_active:
        raise _unauthorized("Invalid or expired token")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
