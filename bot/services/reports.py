"""Queries and presentation helpers for dispatcher reports."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.callbacks import ReportCallback
from bot.constants import CATEGORY_LABELS, STATUS_LABELS, URGENCY_LABELS
from bot.models import Request, RequestEvent, User
from bot.config import DISPLAY_TIMEZONE
from bot.timezone import format_local, local_now


LOCAL_TIMEZONE = ZoneInfo(DISPLAY_TIMEZONE)


@dataclass(frozen=True)
class ReportFilters:
    period: str = "7d"
    category: str = "all"
    urgency: str = "all"
    worker_id: int = 0
    result: str = "all"
    escalation: str = "all"
    custom_start: date | None = None
    custom_end: date | None = None


PERIOD_LABELS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "7d": "Последние 7 дней",
    "30d": "Последние 30 дней",
    "month": "Этот месяц",
    "prev_month": "Прошлый месяц",
    "custom": "Другой период",
}
URGENCY_LABELS = {"high": "высокий", "normal": "обычный", "low": "низкий", "none": "не определён"}
RESULT_LABELS = {"done": "выполнено", "not_done": "не выполнено", "none": "без результата"}
PERIOD_CODES = {"today": "t", "yesterday": "y", "7d": "7", "30d": "30", "month": "m", "prev_month": "pm", "custom": "c"}
CATEGORY_CODES = {
    "all": "a", "electrician": "e", "plumber": "p", "security": "s",
    "cleaning": "c", "kazakhdomofon": "k",
}
URGENCY_CODES = {"all": "a", "high": "h", "normal": "n", "low": "l", "none": "x"}
RESULT_CODES = {"all": "a", "done": "d", "not_done": "n", "none": "x"}
ESCALATION_CODES = {"all": "a", "yes": "y", "no": "n"}


def report_period(filters: ReportFilters) -> tuple[datetime, datetime, str]:
    """Return UTC half-open boundaries and an unambiguous local label."""
    now = local_now()
    today = now.date()
    if filters.period == "today":
        start_date, end_date = today, today + timedelta(days=1)
    elif filters.period == "yesterday":
        start_date, end_date = today - timedelta(days=1), today
    elif filters.period == "30d":
        start_date, end_date = today - timedelta(days=29), today + timedelta(days=1)
    elif filters.period == "month":
        start_date, end_date = today.replace(day=1), today + timedelta(days=1)
    elif filters.period == "prev_month":
        end_date = today.replace(day=1)
        start_date = (end_date - timedelta(days=1)).replace(day=1)
    elif filters.period == "custom" and filters.custom_start and filters.custom_end:
        start_date, end_date = filters.custom_start, filters.custom_end + timedelta(days=1)
    else:
        start_date, end_date = today - timedelta(days=6), today + timedelta(days=1)
    local_start = datetime.combine(start_date, time.min, tzinfo=LOCAL_TIMEZONE)
    local_end = datetime.combine(end_date, time.min, tzinfo=LOCAL_TIMEZONE)
    label_end = end_date - timedelta(days=1)
    label = f"{start_date:%d.%m.%Y}–{label_end:%d.%m.%Y}"
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc), label


def _filtered(stmt, filters: ReportFilters):
    if filters.category != "all":
        stmt = stmt.where(Request.category == filters.category)
    if filters.urgency == "none":
        stmt = stmt.where(Request.urgency.is_(None))
    elif filters.urgency != "all":
        stmt = stmt.where(Request.urgency == filters.urgency)
    if filters.worker_id:
        stmt = stmt.where(Request.worker_id == filters.worker_id)
    if filters.result == "none":
        stmt = stmt.where(Request.completion_result.is_(None))
    elif filters.result != "all":
        stmt = stmt.where(Request.completion_result == filters.result)
    if filters.escalation == "yes":
        stmt = stmt.where(Request.is_escalated.is_(True))
    elif filters.escalation == "no":
        stmt = stmt.where(Request.is_escalated.is_(False))
    return stmt


def _duration(value: float | None) -> str:
    if value is None:
        return "—"
    minutes = max(0, round(value / 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes:02d} мин"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч"


def _active_filter_labels(filters: ReportFilters, worker_name: str | None = None) -> list[str]:
    labels: list[str] = []
    if filters.category != "all":
        labels.append(CATEGORY_LABELS.get(filters.category, filters.category))
    if filters.urgency != "all":
        labels.append(URGENCY_LABELS.get(filters.urgency, filters.urgency))
    if filters.worker_id:
        labels.append(worker_name or f"исполнитель #{filters.worker_id}")
    if filters.result != "all":
        labels.append(RESULT_LABELS.get(filters.result, filters.result))
    if filters.escalation != "all":
        labels.append("эскалированные" if filters.escalation == "yes" else "без эскалации")
    return labels


def _cb(action: str, f: ReportFilters, **changes) -> str:
    values = {
        "action": action,
        "period": f.period,
        "category": f.category,
        "urgency": f.urgency,
        "worker_id": f.worker_id,
        "result": f.result,
        "escalation": f.escalation,
        "start_day": f.custom_start.toordinal() if f.custom_start else 0,
        "end_day": f.custom_end.toordinal() if f.custom_end else 0,
    }
    values.update(changes)
    values["period"] = PERIOD_CODES.get(values["period"], values["period"])
    values["category"] = CATEGORY_CODES.get(values["category"], values["category"])
    values["urgency"] = URGENCY_CODES.get(values["urgency"], values["urgency"])
    values["result"] = RESULT_CODES.get(values["result"], values["result"])
    values["escalation"] = ESCALATION_CODES.get(values["escalation"], values["escalation"])
    return ReportCallback(**values).pack()


def report_keyboard(filters: ReportFilters, active_filters: int) -> InlineKeyboardMarkup:
    period_rows = [
        [("Сегодня", "today"), ("7 дней", "7d"), ("30 дней", "30d")],
        [("Этот месяц", "month"), ("Прошлый", "prev_month"), ("Другой период", "custom")],
    ]
    rows = []
    for choices in period_rows:
        rows.append([
            InlineKeyboardButton(
                text=f"{'✓ ' if filters.period == code else ''}{label}",
                callback_data=_cb("p", filters, period=code),
            )
            for label, code in choices
        ])
    rows.extend([
        [InlineKeyboardButton(text=f"🔎 Фильтры · {active_filters}", callback_data=_cb("f", filters))],
        [
            InlineKeyboardButton(text="🗂 Категории", callback_data=_cb("c", filters)),
            InlineKeyboardButton(text="👷 Исполнители", callback_data=_cb("w", filters)),
        ],
        [
            InlineKeyboardButton(text="📈 Динамика", callback_data=_cb("d", filters)),
            InlineKeyboardButton(text="⬇️ CSV", callback_data=_cb("x", filters)),
        ],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=_cb("o", filters))],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _period_requests(
    session: AsyncSession,
    filters: ReportFilters,
    field,
) -> list[Request]:
    start, end, _ = report_period(filters)
    stmt = select(Request).options(selectinload(Request.worker), selectinload(Request.resident))
    stmt = _filtered(stmt, filters).where(field >= start, field < end)
    result = await session.execute(stmt.order_by(field.desc()))
    return list(result.scalars().unique())


async def build_overview(session: AsyncSession, filters: ReportFilters) -> tuple[str, InlineKeyboardMarkup]:
    start, end, date_label = report_period(filters)
    worker_name = None
    if filters.worker_id:
        worker_name = await session.scalar(select(User.full_name).where(User.id == filters.worker_id))
    labels = _active_filter_labels(filters, worker_name)
    created = await _period_requests(session, filters, Request.created_at)
    closed = await _period_requests(session, filters, Request.closed_at)
    escalated = await _period_requests(session, filters, Request.escalated_at)
    done = sum(r.completion_result == "done" for r in closed)
    not_done = sum(r.completion_result == "not_done" for r in closed)
    unresolved = sum(r.completion_result is None for r in closed)

    event_result = await session.execute(
        select(RequestEvent).where(
            RequestEvent.request_id.in_([r.id for r in created] or [-1]),
            RequestEvent.action.in_(("claimed", "assigned")),
        ).order_by(RequestEvent.request_id, RequestEvent.created_at)
    )
    first_events: dict[int, datetime] = {}
    for event in event_result.scalars():
        first_events.setdefault(event.request_id, event.created_at)
    first_response = [
        (first_events[r.id] - r.created_at).total_seconds()
        for r in created if r.id in first_events
    ]
    resolution = [(r.closed_at - r.created_at).total_seconds() for r in closed]

    active_stmt = _filtered(
        select(Request).where(Request.status.in_(("new", "accepted"))), filters
    )
    active = list((await session.execute(active_stmt)).scalars())
    now_utc = datetime.now(timezone.utc)
    old = sum((now_utc - r.created_at.replace(tzinfo=r.created_at.tzinfo or timezone.utc)) >= timedelta(days=3) for r in active)

    lines = [
        "📊 <b>Отчёт по заявкам</b>",
        f"{PERIOD_LABELS.get(filters.period, 'Период')} · {date_label}",
        f"Фильтры: {', '.join(labels) if labels else 'все'}",
        "",
        "<b>ПОТОК ЗАЯВОК</b>",
        f"🆕 Поступило: <b>{len(created)}</b>",
        f"✅ Завершено: <b>{len(closed)}</b>",
        f"➕ Поступило − завершено: <b>{len(created) - len(closed):+d}</b>",
        f"🚨 Эскалировано: <b>{len(escalated)}</b>",
        "",
        "<b>СКОРОСТЬ</b>",
        f"Первый ответ, медиана: <b>{_duration(median(first_response) if first_response else None)}</b>",
        f"Решение, медиана: <b>{_duration(median(resolution) if resolution else None)}</b>",
        "",
        "<b>РЕЗУЛЬТАТ</b>",
        f"Выполнено: {done} · Не выполнено: {not_done} · Без результата: {unresolved}",
        "",
        "<b>АКТИВНО СЕЙЧАС</b>",
        f"Новых: {sum(r.status == 'new' for r in active)} · В работе: {sum(r.status == 'accepted' for r in active)}",
        f"Старше 3 дней: <b>{old}</b>",
    ]
    return "\n".join(lines), report_keyboard(filters, len(labels))


async def build_filter_screen(session: AsyncSession, filters: ReportFilters) -> tuple[str, InlineKeyboardMarkup]:
    worker_name = None
    if filters.worker_id:
        worker_name = await session.scalar(select(User.full_name).where(User.id == filters.worker_id))
    labels = _active_filter_labels(filters, worker_name)
    rows = [
        [InlineKeyboardButton(text=f"🗂 Категория · {CATEGORY_LABELS.get(filters.category, 'Все') if filters.category != 'all' else 'Все'}", callback_data=_cb("fc", filters))],
        [InlineKeyboardButton(text=f"🔥 Приоритет · {URGENCY_LABELS.get(filters.urgency, 'Все') if filters.urgency != 'all' else 'Все'}", callback_data=_cb("fu", filters))],
        [InlineKeyboardButton(text=f"👷 Исполнитель · {worker_name or 'Все'}", callback_data=_cb("fw", filters))],
        [InlineKeyboardButton(text=f"🎯 Результат · {RESULT_LABELS.get(filters.result, 'Все') if filters.result != 'all' else 'Все'}", callback_data=_cb("fr", filters))],
        [InlineKeyboardButton(text=f"🚨 Эскалация · {('Да' if filters.escalation == 'yes' else 'Нет') if filters.escalation != 'all' else 'Любая'}", callback_data=_cb("fe", filters))],
        [InlineKeyboardButton(text="✖️ Сбросить", callback_data=_cb("o", ReportFilters(period=filters.period)))],
        [InlineKeyboardButton(text="✅ Применить", callback_data=_cb("o", filters))],
    ]
    text = "\n".join([
        "🔎 <b>Фильтры отчёта</b>", "",
        f"Период: {PERIOD_LABELS.get(filters.period, filters.period)}",
        f"Активно: {', '.join(labels) if labels else 'нет'}",
        "", "Выберите измерение. Фильтры сохраняются при переходе между разделами.",
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def choice_keyboard(filters: ReportFilters, kind: str, choices: list[tuple[str, str | int]]) -> InlineKeyboardMarkup:
    rows = []
    for label, value in choices:
        changes = {kind: value}
        rows.append([InlineKeyboardButton(text=label, callback_data=_cb("f", filters, **changes))])
    rows.append([InlineKeyboardButton(text="◀️ К фильтрам", callback_data=_cb("f", filters))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def worker_choices(session: AsyncSession) -> list[tuple[str, int]]:
    result = await session.execute(
        select(User).where(User.role == "worker").order_by(User.full_name)
    )
    return [("Все исполнители", 0), *[(u.full_name, u.id) for u in result.scalars()]]


async def build_breakdown(session: AsyncSession, filters: ReportFilters, kind: str) -> tuple[str, InlineKeyboardMarkup]:
    _, _, date_label = report_period(filters)
    period_requests = await _period_requests(session, filters, Request.created_at)
    if kind == "workers":
        groups: dict[str, list[Request]] = {}
        for req in period_requests:
            groups.setdefault(req.worker.full_name if req.worker else "Без исполнителя", []).append(req)
        title = "👷 <b>Работа исполнителей</b>"
    else:
        groups = {}
        for req in period_requests:
            groups.setdefault(CATEGORY_LABELS.get(req.category, req.category), []).append(req)
        title = "🗂 <b>По категориям</b>"
    lines = [title, date_label, ""]
    if not groups:
        lines.append("За период заявок нет.")
    for label, items in sorted(groups.items()):
        lines.append(
            f"<b>{label}</b>: поступило {len(items)} · "
            f"из них завершено {sum(r.status == 'closed' for r in items)}"
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К отчёту", callback_data=_cb("o", filters))]])
    return "\n".join(lines), kb


async def build_dynamics(session: AsyncSession, filters: ReportFilters) -> tuple[str, InlineKeyboardMarkup]:
    start, end, date_label = report_period(filters)
    created_requests = await _period_requests(session, filters, Request.created_at)
    closed_requests = await _period_requests(session, filters, Request.closed_at)
    days = (end.astimezone(LOCAL_TIMEZONE).date() - start.astimezone(LOCAL_TIMEZONE).date()).days
    bucket = "day" if days <= 45 else "month"
    counts: dict[str, list[int]] = {}
    for req in created_requests:
        created = req.created_at.replace(tzinfo=req.created_at.tzinfo or timezone.utc)
        local = created.astimezone(LOCAL_TIMEZONE)
        key = local.strftime("%d.%m" if bucket == "day" else "%m.%Y")
        counts.setdefault(key, [0, 0])[0] += 1
    for req in closed_requests:
        closed = req.closed_at.replace(tzinfo=req.closed_at.tzinfo or timezone.utc)
        local = closed.astimezone(LOCAL_TIMEZONE)
        key = local.strftime("%d.%m" if bucket == "day" else "%m.%Y")
        counts.setdefault(key, [0, 0])[1] += 1
    lines = ["📈 <b>Динамика</b>", date_label, "", "Дата        Поступило · Завершено"]
    for key, values in counts.items():
        lines.append(f"<code>{key:10} {values[0]:5} · {values[1]:5}</code>")
    if not counts:
        lines.append("За период заявок нет.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К отчёту", callback_data=_cb("o", filters))]])
    return "\n".join(lines), kb


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


async def export_csv(session: AsyncSession, filters: ReportFilters) -> BufferedInputFile:
    start, end, _ = report_period(filters)
    stmt = select(Request).options(selectinload(Request.worker), selectinload(Request.resident))
    stmt = _filtered(stmt, filters).where(Request.created_at >= start, Request.created_at < end)
    requests = list((await session.execute(stmt.order_by(Request.created_at.desc()))).scalars().unique())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Номер", "Создана", "Принята", "Завершена", "Категория",
        "Приоритет", "Статус", "Эскалирована", "Квартира", "Житель",
        "Исполнитель", "Результат", "Описание", "Комментарий исполнителя",
    ])
    for req in requests:
        writer.writerow([
            req.id,
            format_local(req.created_at, "%Y-%m-%d %H:%M"),
            format_local(req.accepted_at, "%Y-%m-%d %H:%M"),
            format_local(req.closed_at, "%Y-%m-%d %H:%M"),
            CATEGORY_LABELS.get(req.category, "Неизвестная категория"),
            URGENCY_LABELS.get(req.urgency, "Не определён"),
            STATUS_LABELS.get(req.status, "Неизвестный статус"),
            "Да" if req.is_escalated else "Нет",
            _csv_safe(req.resident.apartment), _csv_safe(req.resident.full_name),
            _csv_safe(req.worker.full_name if req.worker else ""),
            {"done": "Выполнено", "not_done": "Не выполнено"}.get(
                req.completion_result, "Без результата"
            ),
            _csv_safe(req.description), _csv_safe(req.completion_comment),
        ])
    filename = f"заявки_{start.astimezone(LOCAL_TIMEZONE):%Y-%m-%d}_{(end - timedelta(days=1)).astimezone(LOCAL_TIMEZONE):%Y-%m-%d}.csv"
    return BufferedInputFile(output.getvalue().encode("utf-8-sig"), filename=filename)
