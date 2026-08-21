"""audit log

Revision ID: 2db8b17b7f86
Revises: 3f245130888b
Create Date: 2026-08-21 15:01:06.808940

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2db8b17b7f86'
down_revision: Union[str, Sequence[str], None] = '3f245130888b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    permission_audit -> audit_log, widened.

    A RENAME, not a new table beside the old one. The 119 rows already
    in permission_audit are real history - scope grants, tool toggles,
    approvals - and history that lives in a table nobody queries any
    more has been thrown away without deleting it.

    Every step below preserves those rows. The two DROPs at the end
    happen only after their contents have been copied into the columns
    that replace them.
    """

    op.rename_table("permission_audit", "audit_log")

    # --- renames: same data, better names ----------------------------
    op.alter_column(
        "audit_log", "created_at", new_column_name="occurred_at"
    )
    op.alter_column(
        "audit_log", "ip_address", new_column_name="actor_ip"
    )

    # --- the widening ------------------------------------------------
    op.add_column(
        "audit_log",
        sa.Column("resource_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("resource_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column("request_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "audit_log",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )

    # action was String(32); the new vocabulary is longer
    # ("token.reuse_detected" is 20, but "plugin.refresh_failed" is 21
    # and the next one might not be).
    op.alter_column(
        "audit_log",
        "action",
        type_=sa.String(length=48),
        existing_type=sa.String(length=32),
    )

    # user_id becomes nullable: a failed login has no user yet, and
    # recording "somebody tried to sign in as an address that does not
    # exist" is the whole point of LOGIN_FAILED.
    op.alter_column(
        "audit_log", "user_id", existing_type=sa.Uuid(), nullable=True
    )

    # --- BACKFILL, before anything is dropped ------------------------
    #
    # Every existing row was about an agent, and carried its scope or
    # tool name in a dedicated column. Both facts move without loss:
    # the agent becomes the resource, and the two columns become the
    # metadata they always were.
    op.execute(
        """
        UPDATE audit_log
        SET resource_type = 'agent',
            resource_id   = agent_id,
            metadata      = (
                COALESCE(metadata, '{}'::jsonb)
                || CASE WHEN scope IS NOT NULL
                        THEN jsonb_build_object('scope', scope)
                        ELSE '{}'::jsonb END
                || CASE WHEN tool_name IS NOT NULL
                        THEN jsonb_build_object('tool_name', tool_name)
                        ELSE '{}'::jsonb END
            )
        """
    )

    op.drop_column("audit_log", "agent_id")
    op.drop_column("audit_log", "scope")
    op.drop_column("audit_log", "tool_name")

    # --- indexes ------------------------------------------------------
    # DROP IF EXISTS, not op.drop_index, and the reason is worth
    # knowing: PostgreSQL drops an index automatically when a column it
    # covers is dropped. ix_permission_audit_agent was on
    # (agent_id, created_at), so the drop_column above already took it
    # - and op.drop_index then fails on an index that no longer exists,
    # rolling back the whole migration.
    #
    # The created_at index SURVIVES, because renaming a column does not
    # touch its indexes; only its name is now misleading.
    op.execute("DROP INDEX IF EXISTS ix_permission_audit_agent")
    op.execute("DROP INDEX IF EXISTS ix_permission_audit_created_at")

    op.create_index(
        "ix_audit_log_resource",
        "audit_log",
        ["resource_type", "resource_id", sa.literal_column("occurred_at DESC")],
    )
    op.create_index(
        "ix_audit_log_actor",
        "audit_log",
        ["actor_user_id", sa.literal_column("occurred_at DESC")],
    )
    op.create_index("ix_audit_log_request", "audit_log", ["request_id"])
    op.create_index(
        op.f("ix_audit_log_occurred_at"), "audit_log", ["occurred_at"]
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"])
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"])


def downgrade() -> None:
    """
    Reverses the shape. The widened rows LOSE information.

    A login failure has no agent_id, so it cannot exist in
    permission_audit at all - those rows are deleted rather than
    silently given a fake agent. Stated here because a downgrade that
    quietly discards audit rows is exactly the kind of thing this table
    exists to prevent.
    """

    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_occurred_at"), table_name="audit_log")
    op.drop_index("ix_audit_log_request", table_name="audit_log")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_index("ix_audit_log_resource", table_name="audit_log")

    op.add_column(
        "audit_log", sa.Column("agent_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "audit_log", sa.Column("scope", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "audit_log",
        sa.Column("tool_name", sa.String(length=120), nullable=True),
    )

    op.execute(
        """
        UPDATE audit_log
        SET agent_id  = resource_id,
            scope     = metadata ->> 'scope',
            tool_name = metadata ->> 'tool_name'
        WHERE resource_type = 'agent'
        """
    )

    op.execute("DELETE FROM audit_log WHERE resource_id IS NULL")

    op.alter_column(
        "audit_log", "agent_id", existing_type=sa.Uuid(), nullable=False
    )
    op.alter_column(
        "audit_log", "user_id", existing_type=sa.Uuid(), nullable=False
    )
    op.alter_column(
        "audit_log",
        "action",
        type_=sa.String(length=32),
        existing_type=sa.String(length=48),
    )

    op.drop_column("audit_log", "metadata")
    op.drop_column("audit_log", "request_id")
    op.drop_column("audit_log", "resource_id")
    op.drop_column("audit_log", "resource_type")

    op.alter_column(
        "audit_log", "occurred_at", new_column_name="created_at"
    )
    op.alter_column("audit_log", "actor_ip", new_column_name="ip_address")

    op.rename_table("audit_log", "permission_audit")

    op.create_index(
        "ix_permission_audit_agent",
        "permission_audit",
        ["agent_id", sa.literal_column("created_at DESC")],
    )
    op.create_index(
        "ix_permission_audit_created_at", "permission_audit", ["created_at"]
    )
