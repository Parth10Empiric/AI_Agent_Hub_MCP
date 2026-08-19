from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from api.schemas.user import UserRead


class RegisterRequest(BaseModel):
    """
    What a client sends to create an account.

    Validation happens HERE, before any code runs. FastAPI rejects a
    body that does not match with a 422 and a field-by-field
    explanation, so the service layer can assume its inputs are sane.
    """

    email: EmailStr

    # A minimum length, and a maximum.
    #
    # The maximum is not cosmetic: argon2 hashes whatever it is given,
    # so a 10MB password would cost 10MB of hashing on an unauthenticated
    # endpoint. That is a free denial-of-service. 128 is generous for a
    # real password and cheap to hash.
    password: str = Field(min_length=8, max_length=128)

    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr

    # No min_length here, unlike registration.
    #
    # Rejecting a short password at login with a 422 would tell an
    # attacker their guess failed for a DIFFERENT reason than a wrong
    # password - a small oracle, but a free one to close. Every failed
    # login should look identical.
    password: str = Field(max_length=128)


class RefreshRequest(BaseModel):
    """
    Used by API and CLI clients, which cannot hold cookies.

    Browsers should send the refresh token as an httpOnly cookie
    instead, so no JavaScript can read it. The endpoint accepts either.
    """

    refresh_token: str | None = None


class TokenPair(BaseModel):
    """
    What a successful register / login / refresh returns.

    token_type is "bearer" because that is what the Authorization
    header expects: `Authorization: Bearer <token>`.

    expires_in lets the client refresh BEFORE the token dies, instead
    of discovering it by getting a 401 in the middle of a user action.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthResponse(TokenPair):
    """Tokens plus the user, so a client can render immediately."""

    user: UserRead
