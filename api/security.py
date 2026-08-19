from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from api.settings import APISettings


# Pure functions only: no database, no HTTP, no request object.
#
# Keeping the cryptography free of IO means it can be tested
# exhaustively without a session or a client, and it means nobody can
# accidentally make a database call inside a security check.


ALGORITHM = "HS256"

TOKEN_TYPE_ACCESS = "access"


class TokenError(Exception):
    """
    A token was missing, malformed, expired, signed with the wrong key,
    or of the wrong type.

    Deliberately ONE exception for all of those. The caller turns it
    into a single 401 with one message: distinguishing "expired" from
    "bad signature" in the response tells an attacker which half of
    their guess was right.
    """


_hasher = PasswordHasher()


# Used when the email does not exist, so that path costs the same as a
# real verification.
#
# Without it, login leaks which emails are registered:
#
#     unknown email  -> returns in ~1ms   (no hash performed)
#     wrong password -> returns in ~80ms  (hash performed)
#
# An attacker measures the difference and enumerates the user base
# through the login form. Verifying against this dummy makes both paths
# take the same time.
_DUMMY_HASH = _hasher.hash("a password nobody has")


# ---------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------


def hash_password(raw_password: str) -> str:
    """
    Hash a password with argon2id.

    argon2 is deliberately SLOW and memory-hungry. SHA-256 computes
    roughly a billion hashes per second on a GPU, so a leaked table of
    SHA-256 password hashes is cracked in hours. argon2id is tuned so
    one hash costs milliseconds and tens of megabytes - the memory is
    what defeats GPUs, which have thousands of cores but not thousands
    times 64MB each.

    The returned string carries everything needed to verify it later:

        $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
                       ^^^^^^^^^^^^^^^ cost parameters, stored so the
                                       cost can be raised later and old
                                       hashes still verify

    The salt is random per call, so hashing the same password twice
    gives two different strings. Without that, one cracked hash would
    unlock every account sharing that password.
    """

    return _hasher.hash(raw_password)


def verify_password(stored_hash: str | None, raw_password: str) -> bool:
    """
    Check a password against a stored hash, in constant-ish time.

    Pass stored_hash=None when the user was not found. The dummy hash
    is verified instead, so the caller cannot leak account existence
    through response timing.
    """

    target = stored_hash if stored_hash is not None else _DUMMY_HASH

    try:
        _hasher.verify(target, raw_password)

    except (VerifyMismatchError, InvalidHashError):
        return False

    # A None hash means there was no user. The verification above ran
    # purely to burn the same amount of time; its result must never be
    # reported as success.
    return stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    """
    True when the hash was made with weaker parameters than current.

    Cost parameters should rise as hardware gets faster. Because the
    parameters live inside the hash string, you can raise them and
    upgrade each password hash transparently at the next successful
    login - no password reset, no migration.
    """

    return _hasher.check_needs_rehash(stored_hash)


# ---------------------------------------------------------------------
# Access tokens (JWT)
# ---------------------------------------------------------------------


def create_access_token(
    user_id: uuid.UUID,
    settings: APISettings,
) -> tuple[str, datetime]:
    """
    Mint a short-lived, stateless access token.

    Returns the token and its expiry, so the client can be told when to
    refresh instead of waiting for a 401.

    Stateless means NO database lookup to authenticate a request - the
    signature proves the claims. The trade is that it cannot be
    revoked, which is exactly why it lives for 15 minutes.
    """

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_minutes)

    claims: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": expires_at,

        # Unique per token. Phase 6 can use it to blacklist a single
        # token without inventing an identifier retrospectively.
        "jti": secrets.token_urlsafe(16),

        # Not decoration. Without this claim a REFRESH token would be a
        # perfectly valid access token - same key, same signature - and
        # an attacker holding one could skip the rotation scheme
        # entirely.
        "type": TOKEN_TYPE_ACCESS,
    }

    token = jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=ALGORITHM,
    )

    return token, expires_at


def decode_access_token(token: str, settings: APISettings) -> uuid.UUID:
    """
    Verify an access token and return the user id it names.

    Raises TokenError for every failure mode.
    """

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),

            # NEVER omit this.
            #
            # A JWT carries its own algorithm in its header. Let the
            # library trust that header and an attacker sends
            # {"alg": "none"} and the server accepts an UNSIGNED token.
            # Pinning the algorithm here means the header is ignored.
            algorithms=[ALGORITHM],
        )

    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if claims.get("type") != TOKEN_TYPE_ACCESS:
        raise TokenError("wrong token type")

    subject = claims.get("sub")

    if not subject:
        raise TokenError("missing subject")

    try:
        return uuid.UUID(subject)

    except ValueError as exc:
        raise TokenError("subject is not a user id") from exc


# ---------------------------------------------------------------------
# Refresh tokens (opaque)
# ---------------------------------------------------------------------


def generate_refresh_token() -> str:
    """
    A long random string. NOT a JWT.

    A JWT would be readable and self-validating, which is the opposite
    of what is wanted here: a refresh token must be checked against the
    database so it can be revoked and rotated. There is nothing to
    encode - it is a lookup key.

    secrets.token_urlsafe, never the random module. random is a
    Mersenne Twister; observing a few outputs lets an attacker
    reproduce the sequence and forge future tokens. secrets draws from
    the operating system cryptographic source.
    """

    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """
    SHA-256 of the token, for storage.

    Storing refresh tokens in plaintext means a read-only SQL injection
    or a leaked backup hands over every live session. Hashed, the table
    is worthless to an attacker.

    SHA-256 here rather than argon2 - the opposite of the password
    rule - because this input is 48 bytes of true randomness. There is
    no dictionary to attack, so a slow hash buys nothing and would add
    ~80ms to every refresh.

        argon2      low-entropy human secrets
        sha256      high-entropy random secrets
    """

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
