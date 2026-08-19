from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.models import Request, User
from bot.services.scheduler import check_escalation, escalate_overdue_requests


@pytest.mark.asyncio
async def test_escalation_service_notifies_once(session, fake_bot):
    resident = User(
        telegram_id=901,
        role="resident",
        full_name="Житель <X>",
        apartment="8&B",
        is_approved=True,
    )
    session.add(resident)
    await session.flush()
    request = Request(
        resident_id=resident.id,
        category="plumber",
        description="Течет <кран>",
        status="new",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    session.add(request)
    await session.flush()

    with (
        patch(
            "bot.services.scheduler.notify_workers_force", new=AsyncMock()
        ) as notify_workers,
        patch(
            "bot.services.scheduler.notify_dispatchers", new=AsyncMock()
        ) as notify_dispatchers,
    ):
        first = await escalate_overdue_requests(fake_bot, session, escalation_minutes=20)
        second = await escalate_overdue_requests(fake_bot, session, escalation_minutes=20)

    assert first == 1
    assert second == 0
    notify_workers.assert_awaited_once()
    notify_dispatchers.assert_awaited_once()
    assert "&lt;кран&gt;" in notify_dispatchers.await_args.args[2]
    await session.refresh(request)
    assert request.is_escalated is True


@pytest.mark.asyncio
async def test_check_escalation_accepts_injected_session_factory(engine, fake_bot):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            resident = User(telegram_id=902, role="resident", is_approved=True)
            session.add(resident)
            await session.flush()
            session.add(
                Request(
                    resident_id=resident.id,
                    category="security",
                    description="Дым",
                    status="new",
                    created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                )
            )

    with (
        patch("bot.services.scheduler.notify_workers_force", new=AsyncMock()),
        patch("bot.services.scheduler.notify_dispatchers", new=AsyncMock()),
    ):
        count = await check_escalation(
            fake_bot, session_factory=factory, escalation_minutes=20
        )

    assert count == 1
    async with factory() as session:
        request = (await session.execute(select(Request))).scalar_one()
        assert request.is_escalated is True
