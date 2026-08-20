"""add recurring worker schedules and exceptions

Revision ID: 20260325_05
Revises: 20260324_04
Create Date: 2026-03-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260325_05"
down_revision: Union[str, None] = "20260324_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "worker_working_hours",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_working_hours_weekday"),
        sa.UniqueConstraint("worker_id", "weekday", "start_time", "end_time", name="uq_worker_working_interval"),
    )
    op.create_index("ix_working_hours_worker_weekday", "worker_working_hours", ["worker_id", "weekday"])
    op.create_table(
        "worker_schedule_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ends_at > starts_at", name="ck_schedule_exception_interval"),
    )
    op.create_index(
        "ix_schedule_exception_worker_interval", "worker_schedule_exceptions",
        ["worker_id", "starts_at", "ends_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_exception_worker_interval", table_name="worker_schedule_exceptions")
    op.drop_table("worker_schedule_exceptions")
    op.drop_index("ix_working_hours_worker_weekday", table_name="worker_working_hours")
    op.drop_table("worker_working_hours")
