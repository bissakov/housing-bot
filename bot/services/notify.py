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
from bot.i18n import category_label, render, t
from bot.models import User
from bot.services.identity import delivery_telegram_id
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


async def send_to_user(
    bot: Bot, session: AsyncSession, user: User, text: str, **kwargs
) -> bool:
    """Send to a real user or to the controller of a DEV persona."""
    telegram_id = await delivery_telegram_id(session, user)
    if telegram_id is None:
        return False
    return await _send(bot, telegram_id, text, **kwargs)


async def notify_workers(
    bot: Bot,
    session: AsyncSession,
    category: str,
    text: str,
    force_all: bool = False,
    urgency: str | None = None,
    message_key: str | None = None,
    message_values: dict | None = None,
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
        localized_text = text
        if message_key:
            values = dict(message_values or {})
            if "category" in values:
                values["category"] = category_label(values["category"], worker.language)
            values.setdefault("available_requests", t("available_requests", worker.language))
            localized_text = t(message_key, worker.language, **values)
        send_kwargs = {"parse_mode": "HTML"} if message_key else {}
        telegram_id = await delivery_telegram_id(session, worker, active_only=True)
        if telegram_id is None:
            continue
        if await _send(bot, telegram_id, localized_text, **send_kwargs):
            delivered += 1
        else:
            failed += 1
    return DeliveryReport(delivered, failed)


async def notify_workers_force(
    bot: Bot, session: AsyncSession, category: str, text: str
) -> DeliveryReport:
    return await notify_workers(bot, session, category, text, force_all=True)


async def notify_dispatchers(
    bot: Bot,
    session: AsyncSession,
    text: str,
    *,
    message_key: str | None = None,
    message_values: dict | None = None,
    **kwargs,
) -> DeliveryReport:
    result = await session.execute(
        select(User).where(User.role.in_(("dispatcher", "administrator")))
    )
    db_dispatchers = result.scalars().all()
    recipients = {}
    for user in db_dispatchers:
        telegram_id = await delivery_telegram_id(session, user, active_only=True)
        if telegram_id is not None:
            recipients[telegram_id] = user.language
    recipients.update({telegram_id: None for telegram_id in ADMIN_IDS if telegram_id not in recipients})
    # Synthetic demo accounts have no chat; skip them instead of burning a failed API call each.
    recipients = {tid: language for tid, language in recipients.items() if tid < SEED_TG_START}

    delivered = failed = 0
    for telegram_id, language in recipients.items():
        localized_text = render(message_key, language, message_values) if message_key else text
        if await _send(bot, telegram_id, localized_text, **kwargs):
            delivered += 1
        else:
            failed += 1
    return DeliveryReport(delivered, failed)


async def notify_administrators(
    bot: Bot, session: AsyncSession, text: str, **kwargs
) -> DeliveryReport:
    """Notify internal administrators, presented to users as chairmen."""
    result = await session.execute(
        select(User).where(
            User.role == "administrator", User.is_approved.is_(True)
        )
    )
    recipients = {
        telegram_id
        for user in result.scalars().all()
        if (
            telegram_id := await delivery_telegram_id(
                session, user, active_only=True
            )
        ) is not None
    }
    recipients.update(ADMIN_IDS)
    recipients = {tid for tid in recipients if tid < SEED_TG_START}
    delivered = failed = 0
    for telegram_id in recipients:
        if await _send(bot, telegram_id, text, **kwargs):
            delivered += 1
        else:
            failed += 1
    return DeliveryReport(delivered, failed)


async def notify_resident(
    bot: Bot,
    session: AsyncSession,
    resident: User,
    text: str,
    *,
    language: str | None = None,
    message_key: str | None = None,
    message_values: dict | None = None,
) -> bool:
    localized_text = render(message_key, language, message_values) if message_key else text
    return await send_to_user(bot, session, resident, localized_text)


async def broadcast_announcement(
    bot: Bot, session: AsyncSession, text: str
) -> DeliveryReport:
    result = await session.execute(select(User).where(User.is_approved.is_(True)))
    users = result.scalars().all()

    delivered = failed = 0
    escaped_text = escape(text)
    recipients: dict[int, User] = {}
    for user in users:
        telegram_id = await delivery_telegram_id(session, user, active_only=True)
        if telegram_id is not None:
            recipients[telegram_id] = user

    for telegram_id, user in recipients.items():
        message = t("announcement_broadcast", user.language, text=escaped_text)
        if await _send(bot, telegram_id, message, parse_mode="HTML"):
            delivered += 1
        else:
            failed += 1
    return DeliveryReport(delivered, failed)
