"""cache request translations per target language

Revision ID: 20260824_11
Revises: 20260330_10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_11"
down_revision = "20260330_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_translations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_language", sa.String(length=2), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_language IN ('kk', 'ru')",
            name="ck_request_translations_language",
        ),
        sa.UniqueConstraint(
            "request_id",
            "target_language",
            name="uq_request_translations_request_language",
        ),
    )
    op.create_index(
        "ix_request_translations_request",
        "request_translations",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_translations_request", table_name="request_translations"
    )
    op.drop_table("request_translations")
