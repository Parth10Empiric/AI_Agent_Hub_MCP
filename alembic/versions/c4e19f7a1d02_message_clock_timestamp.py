"""messages.created_at uses clock_timestamp, so a reply cannot sort above its question

Revision ID: c4e19f7a1d02
Revises: b7c41a92e5d3
Create Date: 2026-08-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4e19f7a1d02'
down_revision: Union[str, Sequence[str], None] = 'b7c41a92e5d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Stamp each message when it is INSERTED, not when its transaction began.

    THE ROWS THIS FIXES

        2026-08-26 05:32:10.373557  assistant  "Hmm, my friend..."
        2026-08-26 05:32:10.373557  user       "list out all repo name"

    Identical to the microsecond, because PostgreSQL's now() returns
    the TRANSACTION START time and one agent turn writes the question
    and the answer in a single transaction. An ORDER BY created_at then
    yields whichever the index happens to reach first, so a reply can
    sort above the question it answers.

    It is not theoretical. The chat UI compensates by tie-breaking on
    role, which works - and hides the fact that the stored data cannot
    say which came first. Anything else reading these rows in order
    (an export, a report, the LLM context builder) gets it wrong half
    the time, silently.

    clock_timestamp() reads the wall clock at the moment of the INSERT.
    Still stamped BY THE DATABASE, which is why server_default was
    chosen over a Python default: two application servers with drifting
    clocks must not be able to disagree about the order of one
    conversation.

    Existing rows keep their tied timestamps - there is no information
    left to recover their order from. New ones are unambiguous.
    """

    op.execute(
        "ALTER TABLE messages "
        "ALTER COLUMN created_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    """Back to transaction-start stamping, ties and all."""

    op.execute(
        "ALTER TABLE messages "
        "ALTER COLUMN created_at SET DEFAULT now()"
    )
