import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_service_extension_migration_upgrades_previous_schema(monkeypatch):
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker_category", sa.String(20), nullable=True),
        sa.CheckConstraint(
            "worker_category IS NULL OR worker_category IN "
            "('electrician', 'plumber', 'security')",
            name="ck_users_worker_category",
        ),
    )
    sa.Table(
        "requests",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "category IN ('electrician', 'plumber', 'security')",
            name="ck_requests_category",
        ),
    )
    sa.Table(
        "request_events",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "action IN ('created', 'claimed', 'assigned', 'reassigned', "
            "'closed', 'deleted')",
            name="ck_request_events_action",
        ),
    )
    metadata.create_all(engine)

    migration = importlib.import_module(
        "migrations.versions.20260328_08_service_request_extensions"
    )
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()

        inspector = sa.inspect(connection)
        request_columns = {
            column["name"] for column in inspector.get_columns("requests")
        }
        assert {
            "service_area", "approval_status", "reviewed_by_id",
            "dispatch_after", "dispatched_at",
        }.issubset(request_columns)
        assert "request_attachments" in inspector.get_table_names()
        request_checks = " ".join(
            str(check["sqltext"])
            for check in inspector.get_check_constraints("requests")
        )
        assert "kazakhdomofon" in request_checks
        event_checks = " ".join(
            str(check["sqltext"])
            for check in inspector.get_check_constraints("request_events")
        )
        assert "approved" in event_checks
