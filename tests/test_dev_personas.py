import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from bot.handlers import dev
from bot.models import DevPersona, DevSession, Request, User
from bot.services import identity
from bot.services.identity import get_actor
from bot.services.notify import notify_resident
from bot.services.requests import create_request
from tests.conftest import create_user, make_callback


def test_dev_persona_migration_upgrades_existing_database(monkeypatch):
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    metadata.create_all(engine)
    migration = importlib.import_module(
        "migrations.versions.20260329_09_dev_personas"
    )

    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {"dev_personas", "dev_sessions"}.issubset(
            inspector.get_table_names()
        )
        persona_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("dev_personas")
        }
        assert ("controller_telegram_id", "persona_key") in persona_uniques
        assert ("user_id",) in persona_uniques


@pytest.fixture(autouse=True)
def enable_dev_mode(monkeypatch):
    monkeypatch.setattr(dev, "DEV_MODE", True)
    monkeypatch.setattr(identity, "DEV_MODE", True)


@pytest.mark.asyncio
async def test_switching_personas_preserves_distinct_users_and_history(
    session
):
    controller_id = 77001
    real_user = await create_user(
        session, telegram_id=controller_id, role="worker",
        worker_category="plumber", full_name="Developer",
    )

    await dev.dev_switch(
        make_callback("dev_switch:resident_owner", tg_id=controller_id),
        session,
    )
    resident = await get_actor(session, controller_id)
    assert resident.id != real_user.id
    assert resident.role == "resident"
    assert resident.resident_subrole == "owner"
    assert resident.full_name == "Айдана Қасым"
    assert resident.apartment == "D-01"
    assert resident.language == "kk"

    request = await create_request(
        session,
        resident_id=resident.id,
        category="electrician",
        description="Test request",
    )
    await session.commit()

    await dev.dev_switch(
        make_callback("dev_switch:worker_electrician", tg_id=controller_id),
        session,
    )
    worker = await get_actor(session, controller_id)
    assert worker.id not in {real_user.id, resident.id}
    assert worker.role == "worker"
    assert worker.worker_category == "electrician"
    assert worker.is_on_shift is True

    stored_request = await session.get(Request, request.id)
    stored_resident = await session.get(User, stored_request.resident_id)
    assert stored_resident.id == resident.id
    assert stored_resident.role == "resident"
    assert stored_resident.full_name == "Айдана Қасым"

    await dev.dev_switch(
        make_callback("dev_switch:resident_owner", tg_id=controller_id),
        session,
    )
    assert (await get_actor(session, controller_id)).id == resident.id
    persona_count = await session.scalar(
        select(func.count()).select_from(DevPersona).where(
            DevPersona.controller_telegram_id == controller_id
        )
    )
    assert persona_count == 2


@pytest.mark.asyncio
async def test_persona_notifications_reach_controller_when_inactive(
    session, fake_bot
):
    controller_id = 77002
    await create_user(session, telegram_id=controller_id)
    await dev.dev_switch(
        make_callback("dev_switch:resident_tenant", tg_id=controller_id),
        session,
    )
    resident = await get_actor(session, controller_id)
    await dev.dev_switch(
        make_callback("dev_switch:worker_plumber", tg_id=controller_id),
        session,
    )

    fake_bot.send_message.reset_mock()
    delivered = await notify_resident(
        fake_bot, session, resident, "Persona notification"
    )

    assert delivered is True
    fake_bot.send_message.assert_awaited_once_with(
        controller_id, "Persona notification"
    )


@pytest.mark.asyncio
async def test_return_to_self_and_cleanup_keep_persona_row(session):
    controller_id = 77003
    real_user = await create_user(session, telegram_id=controller_id)
    real_user_id = real_user.id
    await dev.dev_switch(
        make_callback("dev_switch:resident_owner", tg_id=controller_id),
        session,
    )
    persona_user = await get_actor(session, controller_id)
    request = await create_request(
        session,
        resident_id=persona_user.id,
        category="security",
        description="Cleanup request",
    )
    await session.commit()
    request_id = request.id
    persona_user_id = persona_user.id

    await dev.dev_persona_reset(
        make_callback("dev_persona_reset", tg_id=controller_id), session
    )
    assert await session.get(Request, request_id) is None
    assert await session.get(User, persona_user_id) is not None

    await dev.dev_switch(
        make_callback("dev_switch:self", tg_id=controller_id), session
    )
    assert (await get_actor(session, controller_id)).id == real_user_id
    assert await session.get(DevSession, controller_id) is None
