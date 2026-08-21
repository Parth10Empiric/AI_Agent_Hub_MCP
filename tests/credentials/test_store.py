from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.fernet import Fernet  # noqa: E402

from agent.execution import redact_arguments  # noqa: E402
from api.credentials import (  # noqa: E402
    CredentialError,
    CredentialStore,
    FernetCredentialStore,
)

"""
Tests for the credential store and key rotation.

No database, no network. Everything here is about the property that
makes a key rotation possible at all - encrypt with the newest key,
decrypt with any configured key - and about the failure that property
is designed to prevent: dropping the old key too early.
"""


KEY_OLD = Fernet.generate_key().decode()
KEY_NEW = Fernet.generate_key().decode()
KEY_OTHER = Fernet.generate_key().decode()

SECRET = {"access_token": "ghp_realtoken", "refresh_token": "r-1"}


# ---------------------------------------------------------------------
# The basics
# ---------------------------------------------------------------------


def test_it_satisfies_the_store_protocol():
    # The seam that lets Phase 6 swap in Vault or AWS Secrets Manager
    # by writing one class instead of editing every call site.
    assert isinstance(FernetCredentialStore(KEY_OLD), CredentialStore)


def test_a_round_trip_returns_the_same_dict():
    store = FernetCredentialStore(KEY_OLD)

    assert store.decrypt(store.encrypt(SECRET)) == SECRET


def test_the_ciphertext_does_not_contain_the_secret():
    store = FernetCredentialStore(KEY_OLD)

    blob = store.encrypt(SECRET)

    # The milestone assertion, at the unit level: whatever reaches the
    # database, it is not the token.
    assert b"ghp_realtoken" not in blob
    assert b"r-1" not in blob
    assert b"access_token" not in blob


def test_a_dict_is_stored_so_the_payload_can_grow():
    # Phase 3 stored {"credential": "..."}; Phase 5.3 stores an access
    # token, a refresh token and an expiry. Encrypting a STRUCTURE is
    # why that needed no migration - the column is opaque bytes either
    # way.
    store = FernetCredentialStore(KEY_OLD)

    payload = {"access_token": "a", "refresh_token": "b", "expires_at": None}

    assert store.decrypt(store.encrypt(payload)) == payload


def test_the_wrong_key_cannot_read_it():
    written = FernetCredentialStore(KEY_OLD).encrypt(SECRET)

    try:
        FernetCredentialStore(KEY_OTHER).decrypt(written)
        raise AssertionError("an unrelated key decrypted a credential")

    except CredentialError:
        pass


def test_tampered_ciphertext_is_refused():
    store = FernetCredentialStore(KEY_OLD)

    blob = bytearray(store.encrypt(SECRET))
    blob[-1] ^= 0xFF

    # Fernet authenticates the ciphertext, so a single flipped bit is
    # a refusal rather than garbage plaintext. That is the reason to
    # use it over raw AES.
    try:
        store.decrypt(bytes(blob))
        raise AssertionError("tampered ciphertext was accepted")

    except CredentialError:
        pass


def test_a_missing_key_fails_at_construction():
    # At STARTUP, not on the first connect attempt. build_credential_store
    # runs during the lifespan, so a misconfigured deployment refuses to
    # boot instead of failing on request 400.
    for empty in ("", "   ", []):
        try:
            FernetCredentialStore(empty)
            raise AssertionError("an empty key list was accepted")

        except CredentialError:
            pass


def test_a_malformed_key_fails_at_construction():
    try:
        FernetCredentialStore("not-a-fernet-key")
        raise AssertionError("a malformed key was accepted")

    except CredentialError:
        pass


# ---------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------


def test_the_version_is_the_number_of_configured_keys():
    assert FernetCredentialStore(KEY_OLD).version == 1
    assert FernetCredentialStore([KEY_NEW, KEY_OLD]).version == 2


def test_during_a_rotation_both_keys_read_and_the_new_one_writes():
    """
    The property the entire rotation rests on.

    Deploy KEYS=new,old and NOTHING breaks: rows written under `old`
    still decrypt, and everything written from now on uses `new`.
    """

    old_store = FernetCredentialStore(KEY_OLD)
    both = FernetCredentialStore([KEY_NEW, KEY_OLD])

    legacy = old_store.encrypt(SECRET)

    # reads the old row...
    assert both.decrypt(legacy) == SECRET

    # ...and writes with the new key, which the old store cannot read.
    fresh = both.encrypt(SECRET)

    try:
        old_store.decrypt(fresh)
        raise AssertionError("the retired key could still read a new row")

    except CredentialError:
        pass


def test_rotate_moves_a_row_to_the_new_key():
    old_store = FernetCredentialStore(KEY_OLD)
    both = FernetCredentialStore([KEY_NEW, KEY_OLD])
    new_only = FernetCredentialStore(KEY_NEW)

    legacy = old_store.encrypt(SECRET)

    # Before: the new key alone cannot read it.
    try:
        new_only.decrypt(legacy)
        raise AssertionError("the new key read a row it never wrote")

    except CredentialError:
        pass

    rotated = both.rotate(legacy)

    # After: it can, which is what makes it safe to drop the old key.
    assert new_only.decrypt(rotated) == SECRET


def test_rotate_never_exposes_the_plaintext():
    # MultiFernet.rotate decrypts and re-encrypts inside one call, so
    # the rotation job never holds a token in a variable it could log,
    # print, or leave in a traceback.
    both = FernetCredentialStore([KEY_NEW, KEY_OLD])

    legacy = FernetCredentialStore(KEY_OLD).encrypt(SECRET)

    rotated = both.rotate(legacy)

    assert b"ghp_realtoken" not in rotated
    assert rotated != legacy


def test_rotating_an_unreadable_row_raises_rather_than_destroying_it():
    """
    The one mistake that actually loses data.

    A row written with a key nobody configured any more cannot be
    re-encrypted. The job must REFUSE, not overwrite - the right key
    may still exist on someone's laptop, and an overwrite makes the
    credential unrecoverable forever.
    """

    stranger = FernetCredentialStore(KEY_OTHER).encrypt(SECRET)

    both = FernetCredentialStore([KEY_NEW, KEY_OLD])

    try:
        both.rotate(stranger)
        raise AssertionError("a row was rotated under the wrong key")

    except CredentialError:
        pass


def test_dropping_the_old_key_too_early_is_what_breaks():
    # Not a bug - a documented consequence, asserted so the three-state
    # deploy in scripts/rotate_credentials.py is never "simplified"
    # into two.
    legacy = FernetCredentialStore(KEY_OLD).encrypt(SECRET)

    try:
        FernetCredentialStore(KEY_NEW).decrypt(legacy)
        raise AssertionError("a row survived losing the key that wrote it")

    except CredentialError:
        pass


def test_key_order_decides_which_key_writes():
    # Reversed, a rotation would keep writing with the key it is trying
    # to retire - and would look like it was working.
    newest_first = FernetCredentialStore([KEY_NEW, KEY_OLD])
    reversed_ = FernetCredentialStore([KEY_OLD, KEY_NEW])

    assert FernetCredentialStore(KEY_NEW).decrypt(
        newest_first.encrypt(SECRET)
    ) == SECRET

    assert FernetCredentialStore(KEY_OLD).decrypt(
        reversed_.encrypt(SECRET)
    ) == SECRET


# ---------------------------------------------------------------------
# The other leak paths
# ---------------------------------------------------------------------


def test_tool_arguments_carrying_secrets_are_redacted():
    """
    The second leak path, and the one that reaches the LLM.

    redact_arguments runs where the ExecutionRecord is built, so a
    credential passed as a tool argument never reaches the database,
    the timeline, or the model's context.
    """

    redacted = redact_arguments(
        {
            "token": "ghp_x",
            "access_token": "gho_x",
            "refresh_token": "r_x",
            "api_key": "k",
            "password": "p",
            "authorization": "Bearer x",
            "client_secret": "s",
            "repo": "hello",
        }
    )

    for key in (
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "password",
        "authorization",
        "client_secret",
    ):
        assert redacted[key] == "***redacted***", key

    # Ordinary arguments are untouched - the approval dialog needs the
    # REAL values to be worth showing.
    assert redacted["repo"] == "hello"


def test_no_api_response_model_exposes_a_credential():
    """
    The third leak path: an endpoint that returns too much.

    The guarantee is the SHAPE of the response model, not a rule
    somebody has to remember - there is no field for a credential, so
    there is no path from the database to an HTTP response.
    """

    from api.schemas.plugin import ConnectionRead, PluginDetail, PluginSummary

    for model in (ConnectionRead, PluginSummary, PluginDetail):
        fields = set(model.model_fields)

        for banned in (
            "credentials_enc",
            "credential",
            "access_token",
            "refresh_token",
            "token",
        ):
            assert banned not in fields, f"{model.__name__}.{banned}"
