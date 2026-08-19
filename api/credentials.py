from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken

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


class CredentialError(Exception):
    """
    A credential could not be decrypted.

    Almost always means CREDENTIAL_ENCRYPTION_KEY changed. The data is
    not corrupt and is not recoverable without the original key - the
    only remedy is for the user to reconnect the service.
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
    """

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())

        except (ValueError, TypeError) as exc:
            raise CredentialError(
                "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. "
                "Generate one with: python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc

    def encrypt(self, payload: dict[str, Any]) -> bytes:
        """
        JSON, then encrypt.

        A dict rather than a bare string because what has to be stored
        differs per service and per phase: today a personal access
        token, in Phase 5 an OAuth access token plus a refresh token
        plus an expiry. Encrypting a structure means that change needs
        no migration - the column is opaque bytes either way.
        """

        return self._fernet.encrypt(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )

    def decrypt(self, blob: bytes) -> dict[str, Any]:
        try:
            return json.loads(self._fernet.decrypt(blob).decode("utf-8"))

        except InvalidToken as exc:
            # Deliberately vague to the caller and specific in the log.
            # InvalidToken means the key is wrong OR the ciphertext was
            # tampered with, and the difference is not something an API
            # response should reveal.
            raise CredentialError(
                "Stored credential could not be decrypted. The "
                "encryption key may have changed; reconnect the service."
            ) from exc


def build_credential_store(settings: APISettings) -> CredentialStore:
    return FernetCredentialStore(
        settings.credential_encryption_key.get_secret_value()
    )
