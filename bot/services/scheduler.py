"""Periodic escalation of unclaimed requests."""

import logging
from html import escape
from datetime import timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import ESCALATION_MINUTES
from bot.database import async_session
from bot.models import Request, User
from bot.services.notify import notify_dispatchers, notify_workers_force
from bot.timezone import utc_now

logger = logging.getLogger(__name__)


async def escalate_overdue_requests(
    bot: Bot,
    session: AsyncSession,
    escalation_minutes: int = ESCALATION_MINUTES,
) -> int:
    """Claim and notify overdue requests once; returns the number escalated.

    The conditional UPDATE is the idempotency boundary. It protects against
    overlapping scheduler invocations, including multiple bot instances.
    """
    cutoff = utc_now() - timedelta(minutes=escalation_minutes)
    result = await session.execute(
        select(Request.id).where(
            Request.status == "new",
            Request.created_at <= cutoff,
            Request.is_escalated.is_(False),
        )
    )
    request_ids = list(result.scalars())
    escalated = 0

    for request_id in request_ids:
        claimed = await session.execute(
            update(Request)
            .where(
                Request.id == request_id,
                Request.status == "new",
                Request.is_escalated.is_(False),
            )
            .values(is_escalated=True, escalated_at=utc_now())
        )
        if claimed.rowcount != 1:
            continue

        request_result = await session.execute(
            select(Request).where(Request.id == request_id)
        )
        request = request_result.scalar_one()
        resident_result = await session.execute(
            select(User).where(User.id == request.resident_id)
        )
        resident = resident_result.scalar_one_or_none()
        address = f"кв. {resident.apartment}" if resident and resident.apartment else "адрес не указан"
        text = (
            f"⚠️ <b>Эскалация заявки #{request.id}</b>\n"
            f"Категория: {escape(request.category)}\n"
            f"Адрес: {escape(address)}\n"
            f"Описание: {escape(request.description[:500])}"
        )
        await notify_workers_force(bot, session, request.category, text)
        await notify_dispatchers(bot, session, text)
        escalated += 1

    return escalated


async def check_escalation(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession] = async_session,
    escalation_minutes: int = ESCALATION_MINUTES,
) -> int:
    try:
        async with session_factory() as session:
            async with session.begin():
                return await escalate_overdue_requests(
                    bot, session, escalation_minutes=escalation_minutes
                )
    except Exception:
        logger.exception("escalation_job_failed")
        return 0


def setup_scheduler(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession] = async_session,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        check_escalation,
        "interval",
        minutes=1,
        kwargs={"bot": bot, "session_factory": session_factory},
        id="request-escalation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
