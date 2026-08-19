"""Telegram notification delivery with structured failure reporting."""

import logging
from dataclasses import dataclass
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import ADMIN_IDS
from bot.models import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryReport:
    delivered: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.delivered + self.failed


async def _send(bot: Bot, telegram_id: int, text: str, **kwargs) -> bool:
    """Send once and classify Telegram failures without logging message text."""
    try:
        await bot.send_message(telegram_id, text, **kwargs)
        return True
    except TelegramRetryAfter as exc:
        logger.warning(
            "telegram_rate_limited recipient=%s retry_after=%s",
            telegram_id,
            exc.retry_after,
        )
    except TelegramForbiddenError:
        logger.info("telegram_recipient_unreachable recipient=%s", telegram_id)
    except TelegramBadRequest as exc:
        logger.error("telegram_bad_request recipient=%s error=%s", telegram_id, exc)
    except Exception:
        logger.exception("telegram_delivery_failed recipient=%s", telegram_id)
    return False


async def notify_workers(
    bot: Bot,
    session: AsyncSession,
    category: str,
    text: str,
    force_all: bool = False,
) -> DeliveryReport:
    query = select(User).where(
        User.role == "worker",
        User.is_approved.is_(True),
        User.worker_category == category,
    )
    if not force_all:
        query = query.where(User.is_on_shift.is_(True))
    result = await session.execute(query)
    workers = result.scalars().all()

    delivered = failed = 0
    for worker in workers:
        if await _send(bot, worker.telegram_id, text):
            delivered += 1
        else:
            failed += 1
    return DeliveryReport(delivered, failed)


async def notify_workers_force(
    bot: Bot, session: AsyncSession, category: str, text: str
) -> DeliveryReport:
    return await notify_workers(bot, session, category, text, force_all=True)


async def notify_dispatchers(
    bot: Bot, session: AsyncSession, text: str
) -> DeliveryReport:
    result = await session.execute(select(User).where(User.role == "dispatcher"))
    db_dispatchers = result.scalars().all()
    recipients = {user.telegram_id for user in db_dispatchers} | set(ADMIN_IDS)

    delivered = failed = 0
    for telegram_id in recipients:
        if await _send(bot, telegram_id, text):
            delivered += 1
        else:
            failed += 1
    return DeliveryReport(delivered, failed)


async def notify_resident(bot: Bot, resident_telegram_id: int, text: str) -> bool:
    return await _send(bot, resident_telegram_id, text)


async def broadcast_announcement(
    bot: Bot, session: AsyncSession, text: str
) -> DeliveryReport:
    result = await session.execute(select(User).where(User.is_approved.is_(True)))
    users = result.scalars().all()

    delivered = failed = 0
    escaped_text = escape(text)
    for user in users:
        message = f"📢 <b>Объявление</b>\n\n{escaped_text}"
        if await _send(bot, user.telegram_id, message, parse_mode="HTML"):
            delivered += 1
        else:
            failed += 1
    return DeliveryReport(delivered, failed)
