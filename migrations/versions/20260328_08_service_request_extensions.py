"""add cleaning and kazakhdomofon request fields

Revision ID: 20260328_08
Revises: 20260327_07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260328_08"
down_revision = "20260327_07"
branch_labels = None
depends_on = None


def _replace_check(table: str, name: str, expression: str, marker: str) -> None:
    bind = op.get_bind()
    checks = sa.inspect(bind).get_check_constraints(table)
    if any(marker in str(check.get("sqltext", "")) for check in checks):
        return
    existing = {check.get("name") for check in checks}
    with op.batch_alter_table(table) as batch_op:
        if name in existing:
            batch_op.drop_constraint(name, type_="check")
        batch_op.create_check_constraint(name, expression)


def _force_replace_check(table: str, name: str, expression: str) -> None:
    checks = sa.inspect(op.get_bind()).get_check_constraints(table)
    existing = {check.get("name") for check in checks}
    with op.batch_alter_table(table) as batch_op:
        if name in existing:
            batch_op.drop_constraint(name, type_="check")
        batch_op.create_check_constraint(name, expression)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _replace_check(
        "users",
        "ck_users_worker_category",
        "worker_category IS NULL OR worker_category IN "
        "('electrician', 'plumber', 'security', 'cleaning', 'kazakhdomofon')",
        "kazakhdomofon",
    )
    _replace_check(
        "requests",
        "ck_requests_category",
        "category IN "
        "('electrician', 'plumber', 'security', 'cleaning', 'kazakhdomofon')",
        "kazakhdomofon",
    )
    _replace_check(
        "request_events",
        "ck_request_events_action",
        "action IN ('created', 'approved', 'rejected', 'claimed', 'assigned', "
        "'reassigned', 'closed', 'deleted')",
        "approved",
    )

    columns = {column["name"] for column in inspector.get_columns("requests")}
    additions = (
        sa.Column("service_area", sa.String(20), nullable=True),
        sa.Column("approval_status", sa.String(20), nullable=True),
        sa.Column("approval_comment", sa.Text(), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("requests", column)

    request_checks = {
        check.get("name")
        for check in sa.inspect(bind).get_check_constraints("requests")
    }
    with op.batch_alter_table("requests") as batch_op:
        if "ck_requests_service_area" not in request_checks:
            batch_op.create_check_constraint(
                "ck_requests_service_area",
                "service_area IS NULL OR service_area IN ('apartment', 'common')",
            )
        if "ck_requests_approval_status" not in request_checks:
            batch_op.create_check_constraint(
                "ck_requests_approval_status",
                "approval_status IS NULL OR approval_status IN "
                "('pending', 'approved', 'rejected')",
            )

    foreign_keys = {
        foreign_key.get("name")
        for foreign_key in sa.inspect(bind).get_foreign_keys("requests")
    }
    if "fk_requests_reviewed_by_id_users" not in foreign_keys:
        with op.batch_alter_table("requests") as batch_op:
            batch_op.create_foreign_key(
                "fk_requests_reviewed_by_id_users",
                "users",
                ["reviewed_by_id"],
                ["id"],
                ondelete="SET NULL",
            )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("requests")}
    if "ix_requests_deferred_dispatch" not in indexes:
        op.create_index(
            "ix_requests_deferred_dispatch",
            "requests",
            ["dispatch_after", "dispatched_at", "status"],
        )

    if "request_attachments" not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            "request_attachments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "request_id",
                sa.Integer(),
                sa.ForeignKey("requests.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("file_id", sa.String(512), nullable=False),
            sa.Column("file_unique_id", sa.String(128), nullable=False),
            sa.Column("media_type", sa.String(20), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "media_type IN ('photo', 'video', 'document')",
                name="ck_request_attachments_media_type",
            ),
        )
        op.create_index(
            "ix_request_attachments_request",
            "request_attachments",
            ["request_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(
        sa.text(
            "DELETE FROM request_events WHERE action IN ('approved', 'rejected')"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM requests WHERE category IN ('cleaning', 'kazakhdomofon')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE users SET worker_category = NULL "
            "WHERE worker_category IN ('cleaning', 'kazakhdomofon')"
        )
    )
    if "request_attachments" in set(sa.inspect(bind).get_table_names()):
        op.drop_index(
            "ix_request_attachments_request", table_name="request_attachments"
        )
        op.drop_table("request_attachments")
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("requests")}
    if "ix_requests_deferred_dispatch" in indexes:
        op.drop_index("ix_requests_deferred_dispatch", table_name="requests")
    with op.batch_alter_table("requests") as batch_op:
        batch_op.drop_constraint(
            "fk_requests_reviewed_by_id_users", type_="foreignkey"
        )
        batch_op.drop_constraint("ck_requests_service_area", type_="check")
        batch_op.drop_constraint("ck_requests_approval_status", type_="check")
        for column in (
            "dispatched_at", "dispatch_after", "reviewed_at", "reviewed_by_id",
            "approval_comment", "approval_status", "service_area",
        ):
            batch_op.drop_column(column)
    _force_replace_check(
        "request_events",
        "ck_request_events_action",
        "action IN ('created', 'claimed', 'assigned', 'reassigned', 'closed', 'deleted')",
    )
    _force_replace_check(
        "requests",
        "ck_requests_category",
        "category IN ('electrician', 'plumber', 'security')",
    )
    _force_replace_check(
        "users",
        "ck_users_worker_category",
        "worker_category IS NULL OR worker_category IN "
        "('electrician', 'plumber', 'security')",
    )
