from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from api.settings import APISettings


@runtime_checkable
class CredentialStore(Protocol):
    """
    Turns a dict of secrets into bytes safe to put in a database, and
    back again.

    Every encrypt and decrypt in the application goes through this
    interface. Not for tidiness - so that Phase 6 can replace it with
    AWS Secrets Manager or Vault by writing ONE new class, instead of
    hunting down every place a token was encrypted inline.

    Same seam as EmbeddingProvider, PermissionPolicy and
    MCPSessionProvider: choose the simple implementation, but make the
    swap a substitution rather than a migration.
    """

    def encrypt(self, payload: dict[str, Any]) -> bytes: ...

    def decrypt(self, blob: bytes) -> dict[str, Any]: ...

    @property
    def version(self) -> int: ...


class CredentialError(Exception):
    """
    A credential could not be decrypted.

    Means the key that wrote it is no longer configured. Usually a
    rotation where the OLD key was dropped before every row had been
    re-encrypted - which is why rotation is a two-phase job and why
    both keys must be present for the whole of it.

    The data is not corrupt and is not recoverable without that key.
    The only remedy is for the user to reconnect the service.
    """


class FernetCredentialStore:
    """
    Symmetric encryption with Fernet (AES-128-CBC + HMAC-SHA256).

    Fernet is chosen over raw AES because it is hard to misuse: it
    generates the IV, authenticates the ciphertext, and refuses to
    decrypt anything that has been tampered with. Hand-rolled AES
    usually ends up in ECB mode with no authentication.

    THE KEY IS THE WHOLE SYSTEM

        lose it       every stored credential is unreadable, forever
        leak it       every stored credential is readable by whoever
                      has it - the encryption has bought nothing

    So it lives in .env, never in code and never in the database. If it
    were in the database, an attacker who could read credentials_enc
    could read the key beside it, which is the same as storing tokens
    in plaintext with extra steps.

    WHY IT TAKES A LIST OF KEYS (Phase 5.4)

    Because the second failure above eventually happens to everybody,
    and the answer to a leaked key is to replace it - without an
    outage, and without asking every user to reconnect.

    MultiFernet encrypts with the FIRST key and decrypts with ANY of
    them. That single property is what makes a rotation a background
    job rather than a rewrite:

        BEFORE   KEYS=old            everything written with `old`
        DURING   KEYS=new,old        new writes use `new`, old rows
                                     still readable -> no downtime
        AFTER    KEYS=new            once every row has been
                                     re-encrypted by the rotation job

    Deploy the middle state, run scripts/rotate_credentials.py, then
    deploy the last one. At no point is any credential unreadable.

    ORDER MATTERS AND IS NOT ALPHABETICAL: newest key first. Reverse
    it and you keep writing with the key you are trying to retire.
    """

    __slots__ = ("_fernet", "_version")

    def __init__(self, keys: str | list[str]) -> None:

        # A bare string is still accepted so the single-key case - and
        # every existing test - keeps working untouched.
        key_list = [keys] if isinstance(keys, str) else list(keys)

        cleaned = [k.strip() for k in key_list if k and k.strip()]

        if not cleaned:
            raise CredentialError(
                "No credential encryption key configured. Generate one "
                "with: python -c \"from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())\""
            )

        try:
            fernets = [Fernet(key.encode()) for key in cleaned]

        except (ValueError, TypeError) as exc:
            raise CredentialError(
                "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. "
                "Generate one with: python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc

        self._fernet = MultiFernet(fernets)

        # WHICH key wrote a row, recorded as a number.
        #
        # The COUNT of configured keys, so adding a new one at the
        # front bumps it. That makes "is this row current?" a plain
        # integer comparison - `key_version < store.version` - which is
        # something a batch job can put in a WHERE clause and an index
        # can serve.
        #
        # Storing the key itself, or a hash of it, would be worse: the
        # first is obviously wrong, and the second puts a fingerprint
        # of the key next to the data it protects.
        self._version = len(fernets)

    @property
    def version(self) -> int:
        """The key_version to stamp on anything encrypted right now."""

        return self._version

    def encrypt(self, payload: dict[str, Any]) -> bytes:
        """
        JSON, then encrypt with the NEWEST key.

        A dict rather than a bare string because what has to be stored
        differs per service and per phase: in Phase 3 a personal access
        token, since Phase 5.3 an OAuth access token plus a refresh
        token plus an expiry. Encrypting a structure means that change
        needs no migration - the column is opaque bytes either way.
        """

        return self._fernet.encrypt(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )

    def decrypt(self, blob: bytes) -> dict[str, Any]:
        """Decrypt with whichever configured key wrote it."""

        try:
            return json.loads(self._fernet.decrypt(blob).decode("utf-8"))

        except InvalidToken as exc:
            # Deliberately vague to the caller and specific in the log.
            # InvalidToken means no configured key fits OR the
            # ciphertext was tampered with, and the difference is not
            # something an API response should reveal.
            raise CredentialError(
                "Stored credential could not be decrypted. The "
                "encryption key may have changed; reconnect the service."
            ) from exc

    def rotate(self, blob: bytes) -> bytes:
        """
        Re-encrypt an existing blob under the newest key.

        MultiFernet.rotate decrypts with whichever key fits and
        re-encrypts with the first. The plaintext exists only inside
        that call - it is never returned, never assigned, and so never
        available to be logged by accident.

        That is the whole reason this method exists rather than a
        decrypt() followed by an encrypt() at the call site: the
        rotation job never handles a plaintext credential at all.
        """

        try:
            return self._fernet.rotate(blob)

        except InvalidToken as exc:
            raise CredentialError(
                "A stored credential could not be re-encrypted. The "
                "key that wrote it is not configured."
            ) from exc


def build_credential_store(settings: APISettings) -> CredentialStore:
    return FernetCredentialStore(settings.encryption_keys)
