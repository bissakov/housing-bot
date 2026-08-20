"""Store the timestamp of a request escalation.

Revision ID: 20260327_07
Revises: 20260326_06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260327_07"
down_revision: Union[str, Sequence[str], None] = "20260326_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("requests")}
    if "escalated_at" not in columns:
        op.add_column(
            "requests",
            sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("requests")}
    if "ix_requests_closed_at" not in indexes:
        op.create_index("ix_requests_closed_at", "requests", ["closed_at"])
    if "ix_requests_escalated_at" not in indexes:
        op.create_index("ix_requests_escalated_at", "requests", ["escalated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("requests")}
    if "ix_requests_escalated_at" in indexes:
        op.drop_index("ix_requests_escalated_at", table_name="requests")
    if "ix_requests_closed_at" in indexes:
        op.drop_index("ix_requests_closed_at", table_name="requests")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("requests")}
    if "escalated_at" in columns:
        op.drop_column("requests", "escalated_at")
