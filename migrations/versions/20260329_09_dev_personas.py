"""add stable development personas

Revision ID: 20260329_09
Revises: 20260328_08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260329_09"
down_revision = "20260328_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "dev_personas" not in tables:
        op.create_table(
            "dev_personas",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("controller_telegram_id", sa.BigInteger(), nullable=False),
            sa.Column("persona_key", sa.String(length=40), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "controller_telegram_id", "persona_key",
                name="uq_dev_personas_controller_key",
            ),
            sa.UniqueConstraint("user_id", name="uq_dev_personas_user"),
        )
        op.create_index(
            "ix_dev_personas_controller_telegram_id",
            "dev_personas",
            ["controller_telegram_id"],
        )
    if "dev_sessions" not in tables:
        op.create_table(
            "dev_sessions",
            sa.Column("controller_telegram_id", sa.BigInteger(), nullable=False),
            sa.Column("persona_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["persona_id"], ["dev_personas.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("controller_telegram_id"),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "dev_sessions" in tables:
        op.drop_table("dev_sessions")
    if "dev_personas" in tables:
        op.drop_index(
            "ix_dev_personas_controller_telegram_id", table_name="dev_personas"
        )
        op.drop_table("dev_personas")
