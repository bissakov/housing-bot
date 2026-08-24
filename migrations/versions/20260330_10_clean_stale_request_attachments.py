"""clean stale request attachments

Revision ID: 20260330_10
Revises: 20260329_09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260330_10"
down_revision = "20260329_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if not {"requests", "request_attachments"}.issubset(tables):
        return
    # Besides true orphans, remove attachments left by a deleted request whose
    # integer ID was later reused. Those rows predate the current request.
    op.execute(sa.text("""
        DELETE FROM request_attachments
        WHERE NOT EXISTS (
            SELECT 1
            FROM requests
            WHERE requests.id = request_attachments.request_id
        )
        OR created_at < (
            SELECT requests.created_at
            FROM requests
            WHERE requests.id = request_attachments.request_id
        )
        OR (
            EXISTS (
                SELECT 1
                FROM requests
                WHERE requests.id = request_attachments.request_id
                  AND requests.category = 'kazakhdomofon'
                  AND requests.description = 'Добавление Face ID'
            )
            AND request_attachments.id <> (
                SELECT MAX(newest.id)
                FROM request_attachments AS newest
                WHERE newest.request_id = request_attachments.request_id
            )
        )
    """))


def downgrade() -> None:
    # Deleted stale attachment references cannot be reconstructed.
    pass
