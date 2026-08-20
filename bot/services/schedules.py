"""Worker schedule evaluation and dispatcher-managed schedule operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import DISPLAY_TIMEZONE
from bot.models import User, WorkerScheduleException, WorkerWorkingHour
from bot.timezone import utc_now
from bot.i18n import t

WEEKDAY_LABELS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


@dataclass(frozen=True)
class ScheduleStatus:
    planned: bool
    has_schedule: bool
    exception: WorkerScheduleException | None = None


def _aware_utc(value: datetime) -> datetime:
    """Normalize values from PostgreSQL and timezone-naive SQLite tests."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _matches_hour(hour: WorkerWorkingHour, now: datetime) -> bool:
    try:
        local = now.astimezone(ZoneInfo(hour.timezone))
    except ZoneInfoNotFoundError:
        local = now.astimezone(ZoneInfo(DISPLAY_TIMEZONE))
    current = local.time().replace(tzinfo=None)
    if hour.start_time < hour.end_time:
        return local.weekday() == hour.weekday and hour.start_time <= current < hour.end_time
    # Equal endpoints and overnight intervals both wrap into the next day.
    return (
        (local.weekday() == hour.weekday and current >= hour.start_time)
        or (local.weekday() == (hour.weekday + 1) % 7 and current < hour.end_time)
    )


async def get_schedule_status(
    session: AsyncSession,
    worker: User,
    *,
    at: datetime | None = None,
) -> ScheduleStatus:
    """Resolve exceptions, then recurring hours, for one instant.

    Workers without recurring hours remain planned by default. This preserves the
    existing on-shift workflow while dispatchers roll schedules out gradually.
    """
    now = _aware_utc(at or utc_now())
    exception_result = await session.execute(
        select(WorkerScheduleException)
        .where(
            WorkerScheduleException.worker_id == worker.id,
            WorkerScheduleException.starts_at <= now,
            WorkerScheduleException.ends_at > now,
        )
        .order_by(WorkerScheduleException.created_at.desc(), WorkerScheduleException.id.desc())
        .limit(1)
    )
    exception = exception_result.scalar_one_or_none()
    hours_result = await session.execute(
        select(WorkerWorkingHour).where(WorkerWorkingHour.worker_id == worker.id)
    )
    hours = list(hours_result.scalars().all())
    if exception is not None:
        return ScheduleStatus(bool(exception.is_available), bool(hours), exception)
    if not hours:
        return ScheduleStatus(True, False)
    return ScheduleStatus(any(_matches_hour(hour, now) for hour in hours), True)


async def is_worker_available(
    session: AsyncSession,
    worker: User,
    *,
    at: datetime | None = None,
    require_checked_in: bool = True,
) -> bool:
    if worker.role != "worker" or not worker.is_approved:
        return False
    if require_checked_in and not worker.is_on_shift:
        return False
    return (await get_schedule_status(session, worker, at=at)).planned


async def add_recurring_hours(
    session: AsyncSession,
    worker_id: int,
    weekdays: list[int],
    start_time: time,
    end_time: time,
) -> None:
    for weekday in weekdays:
        existing = await session.execute(
            select(WorkerWorkingHour.id).where(
                WorkerWorkingHour.worker_id == worker_id,
                WorkerWorkingHour.weekday == weekday,
                WorkerWorkingHour.start_time == start_time,
                WorkerWorkingHour.end_time == end_time,
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(
                WorkerWorkingHour(
                    worker_id=worker_id,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    timezone=DISPLAY_TIMEZONE,
                )
            )
    await session.flush()


async def clear_recurring_hours(session: AsyncSession, worker_id: int) -> None:
    await session.execute(
        delete(WorkerWorkingHour).where(WorkerWorkingHour.worker_id == worker_id)
    )


async def add_local_exception(
    session: AsyncSession,
    worker_id: int,
    local_start: datetime,
    local_end: datetime,
    *,
    is_available: bool,
    reason: str | None = None,
    language: str | None = None,
) -> WorkerScheduleException:
    zone = ZoneInfo(DISPLAY_TIMEZONE)
    starts_at = local_start.replace(tzinfo=zone).astimezone(timezone.utc)
    ends_at = local_end.replace(tzinfo=zone).astimezone(timezone.utc)
    if ends_at <= starts_at:
        raise ValueError(t("schedule_end_after_start", language))
    item = WorkerScheduleException(
        worker_id=worker_id,
        starts_at=starts_at,
        ends_at=ends_at,
        is_available=is_available,
        reason=(reason or "").strip()[:200] or None,
    )
    session.add(item)
    await session.flush()
    return item


def parse_recurring_hours(text: str, language: str | None = None) -> tuple[list[int], time, time]:
    """Parse ``1-5 09:00-18:00`` or ``1,3,5 09:00-18:00``."""
    try:
        days_raw, interval = text.strip().split(maxsplit=1)
        start_raw, end_raw = interval.split("-", 1)
        start = time.fromisoformat(start_raw.strip())
        end = time.fromisoformat(end_raw.strip())
        days: set[int] = set()
        for part in days_raw.split(","):
            if "-" in part:
                first, last = (int(value) for value in part.split("-", 1))
                if first > last:
                    raise ValueError
                days.update(range(first, last + 1))
            else:
                days.add(int(part))
        if not days or not days.issubset(set(range(1, 8))):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(t("schedule_hours_format", language)) from exc
    return [day - 1 for day in sorted(days)], start, end


def parse_local_exception(text: str, language: str | None = None) -> tuple[datetime, datetime, str | None]:
    """Parse ``25.03.2026 09:00-18:00 reason`` in the organization timezone."""
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2:
        raise ValueError(t("schedule_exception_format", language))
    date_raw, interval = parts[:2]
    reason = parts[2] if len(parts) == 3 else None
    try:
        start_raw, end_raw = interval.split("-", 1)
        day = datetime.strptime(date_raw, "%d.%m.%Y").date()
        start = datetime.combine(day, time.fromisoformat(start_raw))
        end = datetime.combine(day, time.fromisoformat(end_raw))
        if end <= start:
            end += timedelta(days=1)
    except ValueError as exc:
        raise ValueError(t("schedule_exception_format", language)) from exc
    return start, end, reason
