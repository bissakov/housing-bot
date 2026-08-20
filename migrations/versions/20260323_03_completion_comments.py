"""Add worker completion result and comment fields.

Revision ID: 20260323_03
Revises: 20260322_02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260323_03"
down_revision = "20260322_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite applies ALTER TABLE outside a transactional migration. If startup
    # is interrupted, columns can exist while alembic_version still points to
    # the previous revision. Inspect first so retrying container startup is
    # safe after such a partial migration.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("requests")}
    columns = (
        sa.Column("completion_result", sa.String(20), nullable=True),
        sa.Column("completion_comment", sa.Text(), nullable=True),
        sa.Column("completion_raw_comment", sa.Text(), nullable=True),
        sa.Column("completion_llm_meta", sa.Text(), nullable=True),
    )
    for column in columns:
        if column.name not in existing_columns:
            op.add_column("requests", column)

    constraint_name = "ck_requests_completion_result"
    existing_constraints = {
        constraint.get("name")
        for constraint in sa.inspect(bind).get_check_constraints("requests")
    }
    if constraint_name not in existing_constraints:
        with op.batch_alter_table("requests") as batch_op:
            batch_op.create_check_constraint(
                constraint_name,
                "completion_result IS NULL OR completion_result IN ('done', 'not_done')",
            )


def downgrade() -> None:
    op.drop_constraint("ck_requests_completion_result", "requests", type_="check")
    op.drop_column("requests", "completion_llm_meta")
    op.drop_column("requests", "completion_raw_comment")
    op.drop_column("requests", "completion_comment")
    op.drop_column("requests", "completion_result")
