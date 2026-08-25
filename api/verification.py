from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from core.logging import get_logger


"""
Proving a pasted credential actually works, before claiming it does.

THE BUG THIS EXISTS FOR

    paste "hello-world-1234" into the GitHub token box
      -> 201 Created
      -> the plugins page shows a green "Connected" badge
      -> every agent turn that touches GitHub then fails, several
         screens and several minutes later, with an error the user has
         no way to connect back to what they typed

`connect()` encrypted the string, wrote the row, set status
"connected" and never asked GitHub whether the token was real. The
word "connected" was a description of OUR database, not of any
connection - and the only honest reading of that badge is "we stored
something".

WHAT VERIFICATION MEANS HERE

Not "does this look like a token". A regex on `ghp_` would pass a
revoked token, a token for the wrong account, and a token with none of
the scopes the agent needs - and would fail every fine-grained PAT
GitHub ships next year.

The only thing that can tell you a credential works is the service
that issued it, so each verifier CALLS the service, using the same
kind of request the agent will make:

    github           GET  /user                        (the tool
                                                        github_get_
                                                        authenticated_
                                                        user uses)
    slack            POST /api/auth.test
    google_drive     GET  /drive/v3/about
    google_calendar  GET  /calendar/v3/users/me/calendarList

Using the real API rather than a generic token-introspection endpoint
is deliberate. A Google access token scoped only for Drive is REJECTED
by the userinfo endpoint and works perfectly for Drive; verifying
against userinfo would refuse a credential that does the job. The
question worth answering is not "is this token valid somewhere" but
"can this token do what this plugin needs".

THREE OUTCOMES, NOT TWO

    verified      the service answered as the user's account
    rejected      the service said no. The credential is wrong,
                  revoked, or lacks the access this plugin needs.
                  Their problem, and they can fix it.
    unavailable   we could not ask - timeout, DNS, the service is
                  down, we are rate limited. NOT their problem, and
                  emphatically not a reason to say "connected".

Collapsing the third into either of the others is the tempting
simplification and it is wrong in both directions: called a rejection,
it tells someone their perfectly good token is bad; called a success,
it puts the original lie back.
"""


logger = get_logger(__name__)


# Long enough for a slow API on a slow morning, short enough that a
# hung provider does not hold a request open until a proxy kills it.
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class VerifiedCredential:
    """
    What the service told us about the credential we just proved.

    Both fields come FROM THE SERVICE, never from the browser. The
    connect endpoint used to store whatever `account_label` and
    `scopes` the client sent, which meant the label under a connection
    was a claim by whoever pasted the token rather than a fact about
    the account it opens. Anyone could label a personal token
    "finance@company.com".
    """

    account_label: str | None = None
    scopes: tuple[str, ...] = ()


class CredentialRejected(Exception):
    """
    The service refused this credential.

    Carries a sentence meant for the person who pasted it - which is
    why it names the service and what to do, not an HTTP status.
    """


class VerificationUnavailable(Exception):
    """
    We could not reach the service to ask.

    Deliberately NOT a subclass of CredentialRejected. The caller has
    to tell these apart: one means "fix your token", the other means
    "try again in a minute", and answering the second with the first
    sends people looking for a problem they do not have.
    """


Verifier = Callable[[httpx.AsyncClient, str], Awaitable[VerifiedCredential]]


def _scopes_from_header(response: httpx.Response) -> tuple[str, ...]:
    """
    The scopes a classic token carries, per the OAuth header both
    GitHub and Slack return.

    Absent on GitHub's fine-grained tokens and on some Slack tokens, in
    which case this is empty and the connection simply records no
    scopes - which is honest. Inventing a scope list would be worse
    than having none.
    """

    raw = response.headers.get("x-oauth-scopes", "")

    return tuple(
        scope.strip()
        for scope in raw.replace(",", " ").split()
        if scope.strip()
    )


def _rate_limited(response: httpx.Response) -> bool:
    """
    Is this 403 about the credential, or about how often we asked?

    GitHub answers 403 for both, and the difference matters: one is the
    user's token being wrong, the other is our own traffic. Telling
    somebody their valid token is invalid because a shared rate limit
    tripped is a support ticket nobody can diagnose.
    """

    return response.headers.get("x-ratelimit-remaining") == "0"


async def _verify_github(
    client: httpx.AsyncClient,
    credential: str,
) -> VerifiedCredential:

    response = await client.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {credential}",
            "Accept": "application/vnd.github+json",
        },
    )

    if response.status_code == 403 and _rate_limited(response):
        raise VerificationUnavailable(
            "GitHub is rate limiting us right now, so the token could "
            "not be checked. Try again in a few minutes."
        )

    if response.status_code in (401, 403):
        raise CredentialRejected(
            "GitHub rejected that token. Check that it was copied in "
            "full, has not expired, and has not been revoked."
        )

    if response.status_code >= 400:
        raise VerificationUnavailable(
            f"GitHub answered {response.status_code} when we tried to "
            "check the token. Try again shortly."
        )

    data = response.json()

    return VerifiedCredential(
        account_label=data.get("login") or data.get("email"),
        scopes=_scopes_from_header(response),
    )


async def _verify_slack(
    client: httpx.AsyncClient,
    credential: str,
) -> VerifiedCredential:

    response = await client.post(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {credential}"},
    )

    if response.status_code >= 400:
        raise VerificationUnavailable(
            f"Slack answered {response.status_code} when we tried to "
            "check the token. Try again shortly."
        )

    data = response.json()

    # Slack answers 200 and puts the verdict in the BODY. A status-code
    # check alone passes every invalid token it has ever issued.
    if not data.get("ok"):

        error = str(data.get("error") or "invalid_auth")

        if error in ("ratelimited", "service_unavailable", "fatal_error"):
            raise VerificationUnavailable(
                "Slack could not check the token right now "
                f"({error}). Try again shortly."
            )

        raise CredentialRejected(
            f"Slack rejected that token ({error}). Check that it was "
            "copied in full and has not been revoked."
        )

    team = data.get("team")
    user = data.get("user")

    return VerifiedCredential(
        account_label=(
            f"{user}@{team}" if team and user else (team or user)
        ),
        scopes=_scopes_from_header(response),
    )


def _verify_google(url: str, service: str) -> Verifier:
    """
    Build a verifier that calls one Google API.

    A factory because Drive and Calendar differ only in which endpoint
    proves the token - and which endpoint matters, since a token scoped
    for Drive cannot read a calendar and must not be accepted as a
    calendar connection.
    """

    async def verify(
        client: httpx.AsyncClient,
        credential: str,
    ) -> VerifiedCredential:

        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {credential}"},
        )

        if response.status_code in (401, 403):
            raise CredentialRejected(
                f"Google rejected that token for {service}. Check that "
                "it was copied in full, has not expired, and grants "
                f"access to {service}."
            )

        if response.status_code == 429:
            raise VerificationUnavailable(
                "Google is rate limiting us right now, so the token "
                "could not be checked. Try again in a few minutes."
            )

        if response.status_code >= 400:
            raise VerificationUnavailable(
                f"Google answered {response.status_code} when we tried "
                "to check the token. Try again shortly."
            )

        return VerifiedCredential(
            account_label=_google_label(response.json()),
            # Google does not report a token's scopes on these
            # endpoints. Empty rather than guessed.
            scopes=(),
        )

    return verify


def _google_label(data: dict[str, Any]) -> str | None:
    """
    The account's email, from whichever shape the endpoint returned.

    Drive's /about nests it under "user"; calendarList carries it as
    the id of the primary calendar, which IS the email address.
    """

    user = data.get("user")

    if isinstance(user, dict):
        return user.get("emailAddress") or user.get("displayName")

    for item in data.get("items") or ():
        if isinstance(item, dict) and item.get("primary"):
            return item.get("id")

    return None


# Which verifier proves which plugin.
#
# Keyed by the Phase 2 NAMESPACE, so a plugin_connections row, a
# ToolDefinition and a verifier all agree on one identifier.
VERIFIERS: dict[str, Verifier] = {
    "github": _verify_github,
    "slack": _verify_slack,
    "google_drive": _verify_google(
        "https://www.googleapis.com/drive/v3/about?fields=user",
        "Drive",
    ),
    "google_calendar": _verify_google(
        "https://www.googleapis.com/calendar/v3/users/me/"
        "calendarList?maxResults=1",
        "Calendar",
    ),
}


def can_verify(key: str) -> bool:
    """Is there a way to prove a credential for this plugin?"""

    return key in VERIFIERS


async def verify_credential(
    key: str,
    credential: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> VerifiedCredential:
    """
    Ask the service whether this credential works. Raises if it does not.

    A PLUGIN WITH NO VERIFIER CANNOT BE CONNECTED BY TOKEN.

    That is the strict choice, taken on purpose. The alternative -
    store it and hope - is the behaviour being fixed here, and it would
    creep straight back in through the first plugin somebody adds
    without writing eight lines of verifier. Making the failure loud at
    development time is cheaper than making it quiet at 2am.
    """

    verifier = VERIFIERS.get(key)

    if verifier is None:
        raise VerificationUnavailable(
            f"There is no way to verify a token for '{key}' yet, so it "
            "cannot be connected by pasting one. Use the OAuth flow "
            "for this service."
        )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await verifier(client, credential)

    except (CredentialRejected, VerificationUnavailable):
        raise

    except httpx.HTTPError as exc:
        # Network, DNS, TLS, timeout. Ours or the internet's, never the
        # user's token - so it must not read as a rejection.
        logger.warning("credential check for %s failed: %s", key, exc)

        raise VerificationUnavailable(
            "Could not reach the service to check that token. Check "
            "your connection and try again."
        ) from None

    except Exception as exc:
        # A shape we did not expect - malformed JSON, a field that
        # moved. Treated as "could not ask", because the one thing we
        # must not do on an unknown failure is claim success.
        logger.exception("credential check for %s errored: %s", key, exc)

        raise VerificationUnavailable(
            "Could not check that token. Try again shortly."
        ) from None
