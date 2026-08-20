from datetime import datetime, time, timezone

import pytest

from bot.models import User
from bot.services.schedules import (
    add_local_exception,
    add_recurring_hours,
    get_schedule_status,
    parse_local_exception,
    parse_recurring_hours,
)


@pytest.mark.asyncio
async def test_worker_without_schedule_remains_planned(session):
    worker = User(telegram_id=801, role="worker", is_approved=True, is_on_shift=True)
    session.add(worker)
    await session.flush()

    status = await get_schedule_status(
        session, worker, at=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc)
    )

    assert status.planned is True
    assert status.has_schedule is False


@pytest.mark.asyncio
async def test_recurring_schedule_uses_organization_local_time(session):
    worker = User(telegram_id=802, role="worker", is_approved=True, is_on_shift=True)
    session.add(worker)
    await session.flush()
    await add_recurring_hours(session, worker.id, [0], time(9), time(18))

    on_shift = await get_schedule_status(
        session, worker, at=datetime(2026, 3, 23, 5, 0, tzinfo=timezone.utc)
    )
    off_shift = await get_schedule_status(
        session, worker, at=datetime(2026, 3, 23, 14, 0, tzinfo=timezone.utc)
    )

    assert on_shift.planned is True  # 10:00 in Asia/Almaty
    assert off_shift.planned is False  # 19:00 in Asia/Almaty


@pytest.mark.asyncio
async def test_overnight_schedule_matches_following_day(session):
    worker = User(telegram_id=803, role="worker", is_approved=True)
    session.add(worker)
    await session.flush()
    await add_recurring_hours(session, worker.id, [0], time(20), time(8))

    status = await get_schedule_status(
        session, worker, at=datetime(2026, 3, 24, 1, 0, tzinfo=timezone.utc)
    )

    assert status.planned is True  # Tuesday 06:00, Monday's overnight shift


@pytest.mark.asyncio
async def test_exception_overrides_recurring_hours(session):
    worker = User(telegram_id=804, role="worker", is_approved=True)
    session.add(worker)
    await session.flush()
    await add_recurring_hours(session, worker.id, [0], time(9), time(18))
    await add_local_exception(
        session,
        worker.id,
        datetime(2026, 3, 23, 9),
        datetime(2026, 3, 23, 18),
        is_available=False,
        reason="отпуск",
    )

    status = await get_schedule_status(
        session, worker, at=datetime(2026, 3, 23, 6, 0, tzinfo=timezone.utc)
    )

    assert status.planned is False
    assert status.exception.reason == "отпуск"


def test_schedule_input_parsers():
    days, starts, ends = parse_recurring_hours("1-5 09:00-18:00")
    assert days == [0, 1, 2, 3, 4]
    assert starts == time(9)
    assert ends == time(18)

    starts_at, ends_at, reason = parse_local_exception(
        "25.03.2026 20:00-08:00 замена"
    )
    assert ends_at.date().day == 26
    assert reason == "замена"


def test_schedule_parser_errors_follow_language():
    with pytest.raises(ValueError, match="Формат"):
        parse_recurring_hours("bad", "ru")
    with pytest.raises(ValueError, match="Пішім"):
        parse_recurring_hours("bad", "kk")
    with pytest.raises(ValueError, match="себеп"):
        parse_local_exception("bad", "kk")
