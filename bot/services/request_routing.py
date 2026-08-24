"""Small, deterministic routing rules for generic requests."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_

from bot.config import DISPLAY_TIMEZONE
from bot.models import Request


CLEANING_NOTICE = (
    "Қазіргі уақытта өтінімді орындау мүмкін емес. Өтінім бірінші жұмыс күні "
    "орындалады. (Бұл хабарлама автоматты режимде жіберілді) клинингтің жұмыс "
    "кестесін еске саламыз: жұмыс күндері 08:00-ден 13:00-ге дейін, сенбі "
    "08:00-ден 12:00-ге дейін, жексенбі демалыс.\n\n"
    "В данный момент заявка не может быть исполнена. Заявка будет исполнена "
    "в первый рабочий день. (Данное сообщение отправлено в автоматическом "
    "режиме) Напоминаем график работы клининга: будние дни с 08:00 до 13:00, "
    "суббота с 08:00 до 12:00, воскресенье выходной."
)

APARTMENT_PAID_NOTICE = (
    "<b>Құрметті тұрғындар!</b>\n\n"
    "Біз сізге сантехник пен электриктің пәтерлер ішіндегі жұмысы ақылы түрде "
    "орындалатынын хабарлаймыз.\n\n"
    "Жұмыстардың құны орындалған жұмыстардың сипаты мен көлеміне қарай "
    "айқындалады және оны пәтер иесі (жалдаушы) жеке төлейді.\n\n"
    "Жалпыүйлік мүлікке қатысты жұмыстар белгіленген тәртіпке сәйкес "
    "орындалады.\n\nТүсінгеніңіз үшін рахмет!\n\n"
    "<b>Уважаемые жители!</b>\n\n"
    "Информируем вас, что работы сантехника и электрика внутри квартир "
    "выполняются за отдельную плату.\n\n"
    "Стоимость работ определяется в зависимости от характера и объёма "
    "выполненных работ и оплачивается собственником (нанимателем) квартиры "
    "отдельно.\n\n"
    "Работы, относящиеся к общедомовому имуществу, выполняются в соответствии "
    "с установленным порядком.\n\nБлагодарим за понимание!"
)


def next_cleaning_dispatch(now: datetime) -> datetime | None:
    """Return UTC dispatch time, or ``None`` while cleaning is open.

    The fixed client schedule is Monday-Friday 08:00-13:00, Saturday
    08:00-12:00, Sunday closed. Official-holiday logic is intentionally absent.
    """
    zone = ZoneInfo(DISPLAY_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(zone)
    weekday = local.weekday()
    opening = time(8, 0)
    closing = time(12, 0) if weekday == 5 else time(13, 0)
    current = local.time().replace(tzinfo=None)

    if weekday < 6 and opening <= current < closing:
        return None
    if weekday < 6 and current < opening:
        target_day = local.date()
    else:
        days = 1
        while (local + timedelta(days=days)).weekday() == 6:
            days += 1
        target_day = (local + timedelta(days=days)).date()
    target = datetime.combine(target_day, opening, tzinfo=zone)
    return target.astimezone(timezone.utc)


def worker_ready_expression(now: datetime):
    """SQL predicate shared by worker queues."""
    return (
        or_(Request.approval_status.is_(None), Request.approval_status == "approved"),
        or_(Request.dispatch_after.is_(None), Request.dispatch_after <= now),
    )


def is_worker_ready(request: Request, now: datetime) -> bool:
    if request.approval_status not in (None, "approved"):
        return False
    if request.dispatch_after is None:
        return True
    dispatch_after = request.dispatch_after
    if dispatch_after.tzinfo is None:
        dispatch_after = dispatch_after.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return dispatch_after <= now
