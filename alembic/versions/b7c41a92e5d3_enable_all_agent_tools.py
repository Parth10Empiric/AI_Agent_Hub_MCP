"""enable all agent tools - scopes are the only gate

Revision ID: b7c41a92e5d3
Revises: 2db8b17b7f86
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7c41a92e5d3'
down_revision: Union[str, Sequence[str], None] = '2db8b17b7f86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Open every agent_tools row.

    THE ROW THIS FIXES

    Until now `enabled` defaulted to `tool.read_only`, so every existing
    agent carries rows like:

        github_create_issue   enabled = false

    The per-tool checkbox grid that could flip them back is gone; a user
    grants a SCOPE instead. Without this backfill, switching on "Create
    and change issues on GitHub" would appear to work and change
    nothing, because the executor ANDs the scope with `enabled` and this
    row would still say no. The permission would be granted and the tool
    still dead, with no screen left that explains why.

    A blanket UPDATE is safe precisely because it is not the security
    boundary: agent_scopes is. An agent seeded with read scopes only
    still cannot write after this runs - it just stops being vetoed
    twice, once visibly and once invisibly.
    """

    op.execute("UPDATE agent_tools SET enabled = true WHERE enabled = false")


def downgrade() -> None:
    """
    Restore the read-only default for write tools.

    NOT REVERSIBLE, and deliberately a no-op rather than a guess.

    Which rows were false is not recoverable here: this table stores a
    tool NAME, not its classification, and read_only lives in the MCP
    registry at runtime. Re-deriving it would mean importing the tool
    catalogue into a migration - a schema step that fails when a service
    is unreachable.

    Downgrading past this revision leaves every row enabled. That is the
    permissive direction, so anyone doing it should re-run the tool sync
    with the old default restored in _default_rows.
    """
