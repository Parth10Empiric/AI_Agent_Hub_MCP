from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from api.deps import CurrentUser, DbDep, SettingsDep
from api.schemas.auth import (
    AuthResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from api.schemas.user import UserRead
from api.services import auth_service
from api.services.auth_service import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidRefreshToken,
    IssuedTokens,
)


# HTTP only: read the request, call the service, shape the response,
# choose a status code. No business logic, no SQL.


router = APIRouter(prefix="/api/auth", tags=["auth"])


REFRESH_COOKIE = "refresh_token"


def _client_context(request: Request) -> tuple[str | None, str | None]:
    """User agent and IP, recorded against each refresh token."""

    return (
        request.headers.get("user-agent"),
        request.client.host if request.client else None,
    )


def _set_refresh_cookie(
    response: Response,
    token: str,
    settings: SettingsDep,
) -> None:
    """
    Deliver the refresh token to a browser.

    httpOnly   JavaScript cannot read it. This is the whole point: a
               token in localStorage is readable by any injected
               script, so one XSS costs every session.

    secure     HTTPS only. Off in development because localhost is
               plain HTTP and the cookie would simply never be set.

    samesite   "lax" blocks the cookie on cross-site POSTs, which is
               CSRF protection, while still allowing normal top-level
               navigation. "strict" would break OAuth redirects in
               Phase 5.
    """

    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        path="/api/auth",
    )


def _auth_response(
    issued: IssuedTokens,
    response: Response,
    settings: SettingsDep,
) -> AuthResponse:
    _set_refresh_cookie(response, issued.refresh_token, settings)

    return AuthResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        user=UserRead.model_validate(issued.user),
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: DbDep,
    settings: SettingsDep,
) -> AuthResponse:
    """
    Create an account and log in.

    201, not 200: a resource was created, and the Location of that
    resource is the new user.
    """

    user_agent, ip = _client_context(request)

    try:
        issued = await auth_service.register(
            session,
            settings,
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            user_agent=user_agent,
            ip_address=ip,
        )

    except EmailAlreadyRegistered:
        # 409 Conflict: the request is well-formed but conflicts with
        # existing state.
        #
        # This DOES reveal that an email is registered - unavoidable,
        # since the account cannot be created twice. Phase 5 mitigates
        # it with rate limiting rather than by weakening the response.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from None

    return _auth_response(issued, response, settings)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbDep,
    settings: SettingsDep,
) -> AuthResponse:
    """
    Exchange credentials for a token pair.
    """

    user_agent, ip = _client_context(request)

    try:
        issued = await auth_service.login(
            session,
            settings,
            email=payload.email,
            password=payload.password,
            user_agent=user_agent,
            ip_address=ip,
        )

    except InvalidCredentials:
        # ONE message for wrong password, unknown email and disabled
        # account. Anything more specific turns this endpoint into a
        # way to discover who has an account.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        ) from None

    return _auth_response(issued, response, settings)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    session: DbDep,
    settings: SettingsDep,
) -> TokenPair:
    """
    Rotate the refresh token and mint a new access token.

    The cookie is preferred over the body: a browser sends it
    automatically and cannot read it, so it is the safer of the two.
    The body exists for API and CLI clients, which hold no cookies.
    """

    raw = request.cookies.get(REFRESH_COOKIE) or payload.refresh_token

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided.",
        )

    user_agent, ip = _client_context(request)

    try:
        issued = await auth_service.refresh(
            session,
            settings,
            raw_token=raw,
            user_agent=user_agent,
            ip_address=ip,
        )

    except InvalidRefreshToken:
        # Clear the cookie as well as refusing.
        #
        # If reuse detection fired, the whole family is now revoked and
        # the cookie is worthless. Leaving it in place would make the
        # browser retry with it forever.
        response.delete_cookie(REFRESH_COOKIE, path="/api/auth")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from None

    _set_refresh_cookie(response, issued.refresh_token, settings)

    return TokenPair(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    session: DbDep,
) -> None:
    """
    Revoke the session.

    204 whether or not the token was real. Logout is not a place to
    tell an unauthenticated caller whether a token existed, and a
    client that already lost its token should still be able to clear
    its cookie without seeing an error.
    """

    raw = request.cookies.get(REFRESH_COOKIE) or payload.refresh_token

    if raw:
        await auth_service.logout(session, raw_token=raw)

    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser) -> UserRead:
    """
    The caller, as identified by their access token.

    The entire body is `current_user` - all the work happens in the
    get_current_user dependency. That is the pattern every protected
    endpoint from Phase 3.6 onward follows.
    """

    return UserRead.model_validate(current_user)
