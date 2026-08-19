"""Baseline existing installations and add request audit events.

Revision ID: 20260321_01
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "20260321_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # A fresh installation gets the complete current schema from metadata.
    if "users" not in tables:
        from bot.models import Base

        Base.metadata.create_all(bind=bind)
        return

    if "request_events" not in tables:
        op.create_table(
            "request_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("request_id", sa.Integer(), nullable=False),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "action IN ('created', 'claimed', 'assigned', 'reassigned', 'closed', 'deleted')",
                name="ck_request_events_action",
            ),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_request_events_request_created",
            "request_events",
            ["request_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "request_events" in set(sa.inspect(bind).get_table_names()):
        op.drop_index(
            "ix_request_events_request_created", table_name="request_events"
        )
        op.drop_table("request_events")
