from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.services.permission_service import _options  # noqa: E402
from tests.tool_fixtures import build_registry  # noqa: E402

"""
A permission over a service with no account behind it is not a choice.

THE COMPLAINT THIS COMES FROM

The permissions page listed every scope of every service the MCP
server exposes - four services, dozens of switches - whether or not the
user had connected any of them. Granting `google_drive:*:read` to an
agent when no Google account is connected achieves exactly nothing: the
tool is offered, the model calls it, and it fails at the credential
resolver several screens later.

That is how a security screen becomes a wall of switches, and a wall is
what people click through without reading - on the one page where
reading is the whole point.

MARKED, NOT FILTERED

The server reports `connected` and returns every scope anyway. Two
reasons, and the second is the one that matters:

  An agent can hold a grant over a service that was disconnected
  afterwards. That grant is still recorded and applies again the moment
  the service is reconnected. If the server dropped the scope, the UI
  would have no row to revoke - a permission you cannot see is a
  permission you cannot take away.

  What to HIDE is a presentation question. This screen tucks them into
  a "Not connected" list; an audit view would want all of them. The
  server should not decide that.
"""


class Engine:
    """Just `get_tools`, which is all `_options` reads."""

    def __init__(self) -> None:
        self._registry = build_registry()

    def get_tools(self):
        return self._registry.all()


ENGINE = Engine()


def options_for(connected: set[str] | None, granted: set[str] = frozenset()):
    return _options(ENGINE, set(granted), connected)


def by_service(options, service: str) -> list:
    return [o for o in options if o.service == service]


def test_scopes_of_a_connected_service_are_marked_connected():
    options = options_for({"github"})

    github = by_service(options, "github")

    assert github
    assert all(option.connected for option in github)


def test_scopes_of_an_unconnected_service_are_marked_unconnected():
    options = options_for({"github"})

    drive = by_service(options, "google_drive")

    assert drive
    assert not any(option.connected for option in drive)


def test_nothing_is_removed_from_the_catalogue():
    # The full list is what makes an orphaned grant revocable. Filtering
    # here would take a live permission off the only screen that can
    # revoke it.
    everything = options_for(None)
    narrowed = options_for({"github"})

    assert len(narrowed) == len(everything)


def test_an_orphaned_grant_is_still_reported():
    # Connect GitHub, grant a write, disconnect GitHub. The grant is
    # still real - so it must still be visible AND still be flagged as
    # having nothing behind it.
    options = options_for(set(), granted={"github:*:write"})

    orphan = next(o for o in options if o.scope == "github:*:write")

    assert orphan.granted is True
    assert orphan.connected is False


def test_no_connection_information_means_no_claims():
    # `connected=None` is "the caller did not tell us", used by callers
    # that have no user context. Reporting every scope as unconnected
    # there would be a lie in the other direction.
    assert all(option.connected for option in options_for(None))


def test_the_flag_does_not_disturb_the_rest_of_the_catalogue():
    options = options_for({"slack"}, granted={"slack:*:read"})

    slack_read = next(o for o in options if o.scope == "slack:*:read")

    assert slack_read.granted is True
    assert slack_read.connected is True
    assert slack_read.tool_count > 0
    assert slack_read.action == "read"
