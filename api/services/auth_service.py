from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import RefreshToken, User
from api.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from api.settings import APISettings


# Business logic only. No FastAPI imports, no HTTPException, no Request.
#
# The router turns these errors into status codes. Keeping the logic
# free of HTTP means it can be tested by calling a function, and reused
# later from a CLI, a worker or a scheduled job without pretending to
# be a web request.


class AuthError(Exception):
    """Base for every authentication failure."""


class EmailAlreadyRegistered(AuthError):
    """Registration hit the unique constraint on users.email."""


class InvalidCredentials(AuthError):
    """
    Wrong password, unknown email, or a disabled account.

    ONE exception for all three, on purpose. The endpoint must answer
    identically in every case - saying "no account with that email"
    turns the login form into a tool for discovering who has one.
    """


class InvalidRefreshToken(AuthError):
    """Unknown, expired, already used, or revoked."""


@dataclass(slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


async def _issue_tokens(
    session: AsyncSession,
    user: User,
    settings: APISettings,
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedTokens:
    """
    Mint an access/refresh pair and persist the refresh token.

    family_id=None starts a NEW rotation chain, which is what a fresh
    login does. A refresh passes the existing family through, so the
    whole chain can be revoked together if one of its tokens is later
    replayed.
    """

    access_token, access_expires = create_access_token(user.id, settings)

    raw_refresh = generate_refresh_token()

    session.add(
        RefreshToken(
            # Only the HASH is stored. The raw value below is returned
            # to the client once and then unrecoverable from here.
            token_hash=hash_refresh_token(raw_refresh),
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_days),
            user_agent=(user_agent or "")[:255] or None,
            ip_address=(ip_address or "")[:45] or None,
        )
    )

    expires_in = int(
        (access_expires - datetime.now(timezone.utc)).total_seconds()
    )

    return IssuedTokens(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=expires_in,
        user=user,
    )


async def register(
    session: AsyncSession,
    settings: APISettings,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedTokens:
    """
    Create an account and log it in.
    """

    email = email.strip().lower()

    # A check, then an insert, is a race: two simultaneous requests can
    # both pass this and both try to insert. The UNIQUE index on
    # users.email is what actually prevents a duplicate - this lookup
    # only produces a friendlier error in the common case.
    existing = await session.scalar(select(User).where(User.email == email))

    if existing is not None:
        raise EmailAlreadyRegistered(email)

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
    )

    session.add(user)

    # Assigns the primary key without committing, so the refresh token
    # row below can reference user.id inside the same transaction.
    await session.flush()

    return await _issue_tokens(
        session,
        user,
        settings,
        user_agent=user_agent,
        ip_address=ip_address,
    )


async def login(
    session: AsyncSession,
    settings: APISettings,
    *,
    email: str,
    password: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedTokens:
    """
    Verify credentials and start a new token family.
    """

    email = email.strip().lower()

    user = await session.scalar(select(User).where(User.email == email))

    # Note what does NOT happen here: an early return when the user is
    # missing.
    #
    # verify_password(None, ...) hashes against a dummy so this branch
    # costs the same as a real check. Returning early would make an
    # unknown email answer in ~1ms and a wrong password in ~80ms, and
    # that difference is a user-enumeration oracle.
    if not verify_password(user.password_hash if user else None, password):
        raise InvalidCredentials()

    assert user is not None  # verify_password returns False when None

    if not user.is_active:
        raise InvalidCredentials()

    # Transparent upgrade: if the cost parameters have been raised since
    # this password was last hashed, re-hash it now, while the plaintext
    # is legitimately in hand. No password reset needed.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    return await _issue_tokens(
        session,
        user,
        settings,
        user_agent=user_agent,
        ip_address=ip_address,
    )


async def _revoke_family(
    session: AsyncSession,
    family_id: uuid.UUID,
) -> None:
    """
    Kill every live token in one rotation chain.

    A single UPDATE ... WHERE rather than loading the rows and looping.
    One statement instead of N, and no chance of the set changing
    between the read and the write.
    """

    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )


async def refresh(
    session: AsyncSession,
    settings: APISettings,
    *,
    raw_token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedTokens:
    """
    Exchange a refresh token for a new pair, and detect theft.

    The token presented by the client is hashed and looked up - the
    stored value is never compared in plaintext because plaintext is
    never stored.
    """

    stored = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(raw_token)
        )
    )

    if stored is None:
        raise InvalidRefreshToken("unknown token")

    # REUSE DETECTION
    #
    # This token was already consumed, and it has come back. Either the
    # real client replayed it or an attacker stole it, and there is no
    # way to distinguish the two - so assume theft and revoke the whole
    # family.
    #
    # Both parties are logged out. The user can log in again; the
    # attacker cannot.
    if stored.used_at is not None:
        await _revoke_family(session, stored.family_id)

        # COMMIT BEFORE RAISING.
        #
        # This is not optional, and getting it wrong is silent. The
        # router turns InvalidRefreshToken into an HTTPException, and
        # get_db rolls the session back on ANY exception - which would
        # undo the revocation that was the entire point of detecting
        # the reuse. Theft detection would appear to work, log
        # nothing, and revoke nothing.
        #
        # The revocation is a security action that must outlive the
        # failed request, so it is committed here explicitly.
        await session.commit()

        raise InvalidRefreshToken("token reuse detected")

    if stored.revoked_at is not None:
        raise InvalidRefreshToken("revoked")

    if stored.expires_at <= datetime.now(timezone.utc):
        raise InvalidRefreshToken("expired")

    user = await session.get(User, stored.user_id)

    if user is None or not user.is_active:
        raise InvalidRefreshToken("user unavailable")

    # ROTATION: consume this token, then issue its successor inside the
    # same family.
    stored.used_at = datetime.now(timezone.utc)

    return await _issue_tokens(
        session,
        user,
        settings,
        family_id=stored.family_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )


async def logout(session: AsyncSession, *, raw_token: str) -> None:
    """
    Revoke the presented token and its whole family.

    The family, not just the one token: logging out should end the
    session, and the session is the chain. Revoking only the newest
    token would leave nothing usable in practice, but revoking the
    family states the intent and is robust if a client holds an older
    one.

    Deliberately silent when the token is unknown. Logout is not a
    place to tell an unauthenticated caller whether a token was real.
    """

    stored = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(raw_token)
        )
    )

    if stored is None:
        return

    await _revoke_family(session, stored.family_id)
