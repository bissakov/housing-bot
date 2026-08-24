import importlib
from datetime import datetime

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_stale_attachment_migration_removes_orphans_and_reused_id_rows(
    monkeypatch,
):
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    requests = sa.Table(
        "requests",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    attachments = sa.Table(
        "request_attachments",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(requests.insert(), {
            "id": 84,
            "category": "kazakhdomofon",
            "description": "Добавление Face ID",
            "created_at": datetime(2026, 8, 24, 13, 17),
        })
        connection.execute(requests.insert(), {
            "id": 85,
            "category": "cleaning",
            "description": "Грязный пол у лифта",
            "created_at": datetime(2026, 8, 24, 13, 17),
        })
        connection.execute(attachments.insert(), [
            {
                "id": 1,
                "request_id": 84,
                # SQLite timestamps have one-second precision. The Face ID
                # one-photo rule still identifies this reused-ID duplicate.
                "created_at": datetime(2026, 8, 24, 13, 17),
            },
            {
                "id": 2,
                "request_id": 84,
                "created_at": datetime(2026, 8, 24, 13, 17, 1),
            },
            {
                "id": 3,
                "request_id": 999,
                "created_at": datetime(2026, 8, 24, 11),
            },
            {
                "id": 4,
                "request_id": 85,
                "created_at": datetime(2026, 8, 24, 12),
            },
        ])

        migration = importlib.import_module(
            "migrations.versions.20260330_10_clean_stale_request_attachments"
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()

        remaining = connection.execute(
            sa.select(attachments.c.id).order_by(attachments.c.id)
        ).scalars().all()

    assert remaining == [2]
