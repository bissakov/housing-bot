"""Telegram notification delivery with structured failure reporting."""

import logging
from dataclasses import dataclass
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import ADMIN_IDS
from bot.constants import SEED_TG_START
from bot.models import User
from bot.services.schedules import is_worker_available

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
        # "chat not found" just means this account never opened a dialog with the bot
        # (seed rows, dispatchers added by hand). Not an error, and not worth a traceback.
        if "chat not found" in str(exc).lower():
            logger.info("telegram_recipient_unreachable recipient=%s", telegram_id)
        else:
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
    urgency: str | None = None,
) -> DeliveryReport:
    query = select(User).where(
        User.role == "worker",
        User.is_approved.is_(True),
        User.worker_category == category,
    )
    result = await session.execute(query)
    category_workers = list(result.scalars().all())
    if force_all:
        workers = category_workers
    else:
        workers = [
            worker for worker in category_workers
            if await is_worker_available(session, worker)
        ]
        # Urgent fallback: scheduled but not checked in, then every specialist.
        if not workers and urgency == "high":
            workers = [
                worker for worker in category_workers
                if await is_worker_available(session, worker, require_checked_in=False)
            ]
        if not workers and urgency == "high":
            workers = category_workers

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
    bot: Bot, session: AsyncSession, text: str, **kwargs
) -> DeliveryReport:
    result = await session.execute(
        select(User).where(User.role.in_(("dispatcher", "administrator")))
    )
    db_dispatchers = result.scalars().all()
    recipients = {user.telegram_id for user in db_dispatchers} | set(ADMIN_IDS)
    # Synthetic demo accounts have no chat; skip them instead of burning a failed API call each.
    recipients = {tid for tid in recipients if tid < SEED_TG_START}

    delivered = failed = 0
    for telegram_id in recipients:
        if await _send(bot, telegram_id, text, **kwargs):
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
