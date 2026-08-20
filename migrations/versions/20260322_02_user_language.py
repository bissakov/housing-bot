"""add user language preference

Revision ID: 20260322_02
Revises: 20260321_01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260322_02"
down_revision = "20260321_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "language" not in columns:
        op.add_column("users", sa.Column("language", sa.String(length=2), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}
    checks = {check["name"] for check in inspector.get_check_constraints("users")}
    # The baseline creates the current metadata on a fresh database, so in that
    # case this revision did not add the column and must not remove it.
    if "language" in columns and "ck_users_language" not in checks:
        op.drop_column("users", "language")
