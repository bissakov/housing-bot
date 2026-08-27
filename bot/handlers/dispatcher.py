from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import literal, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from html import escape
from datetime import date, datetime
import logging

from bot.models import User, Request, RequestEvent, WorkerWorkingHour, WorkerScheduleException
from bot.constants import URGENCY_LABELS, REQUEST_CATEGORIES
from bot.states import AnnouncementStates, AddWorkerStates, ReportDateStates, ScheduleStates
from bot.keyboards import (
    CATEGORY_LABELS, STATUS_LABELS,
    dispatcher_request_keyboard, assign_worker_keyboard, category_keyboard, approval_keyboard, cancel_keyboard, reply_cancel_keyboard
)
from bot.services.requests import assign_request, create_announcement
from bot.services.llm import get_llm
from bot.services.request_translations import (
    format_description_html,
    localize_request_description,
    localize_request_descriptions,
)
from bot.services.identity import delivery_telegram_id, get_actor
from bot.services.notify import broadcast_announcement, send_to_user
from bot.services.schedules import (
    WEEKDAY_LABELS,
    add_local_exception,
    add_recurring_hours,
    clear_recurring_hours,
    parse_local_exception,
    parse_recurring_hours,
)
from bot.auth import is_administrator, is_dispatcher
from bot.callbacks import (
    DispatcherRequestCallback,
    DispatcherHistoryCallback,
    DispatcherFilteredRequestCallback,
    ReportCallback,
)
from bot.services.reports import (
    ReportFilters,
    build_breakdown,
    build_dynamics,
    build_filter_screen,
    build_overview,
    choice_keyboard,
    export_csv,
    worker_choices,
)
from bot.timezone import format_local
from bot.config import DISPLAY_TIMEZONE
from bot.i18n import category_label, normalize_language, role_label, t, text_variants

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "cancel_fsm")
async def dispatcher_cancel_cb(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if await state.get_state() is None:
        await callback.answer()
        return
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    u = await get_actor(session, callback.from_user.id)
    kb = get_main_keyboard(u) if u and u.is_approved else None
    try:
        await callback.message.edit_text(t("cancel", u.language if u else None))
    except Exception:
        pass
    if kb:
        await callback.message.answer(t("main_menu", u.language), reply_markup=kb)
    await callback.answer()

@router.message(F.text.in_(text_variants("cancel")))
async def dispatcher_cancel_text(message: Message, state: FSMContext, session: AsyncSession):
    if await state.get_state() is None:
        return
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    u = await get_actor(session, message.from_user.id)
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await message.answer(t("cancelled", u.language if u else None), reply_markup=kb)

PAGE_SIZE = 5


def _is_dispatcher(user: User) -> bool:
    return is_dispatcher(user)


async def _require_dispatcher(event: Message | CallbackQuery, session: AsyncSession) -> bool:
    user = await get_actor(session, event.from_user.id)
    allowed = is_dispatcher(user)
    if not allowed:
        if isinstance(event, CallbackQuery):
            await event.answer(t("insufficient_rights", user.language if user else None), show_alert=True)
        else:
            await event.answer(t("insufficient_rights", user.language if user else None))
    return allowed


async def _event_language(event: Message | CallbackQuery, session: AsyncSession) -> str:
    user = await get_actor(session, event.from_user.id)
    return normalize_language(user.language if user else None)


async def _total_requests(
    session: AsyncSession, status: str = "all", category: str = "all"
) -> int:
    stmt = select(func.count()).select_from(Request)
    if status != "all":
        stmt = stmt.where(Request.status == status)
    if category != "all":
        stmt = stmt.where(Request.category == category)
    res = await session.execute(stmt)
    return res.scalar() or 0


async def build_dispatcher_list(
    session: AsyncSession,
    page: int,
    status: str = "all",
    category: str = "all",
    language: str | None = "ru",
) -> tuple[str, InlineKeyboardMarkup]:
    total = await _total_requests(session, status, category)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    page = max(0, min(page, total_pages - 1))

    stmt = select(Request).options(
        selectinload(Request.resident), selectinload(Request.worker)
    )
    if status != "all":
        stmt = stmt.where(Request.status == status)
    if category != "all":
        stmt = stmt.where(Request.category == category)
    stmt = (
        stmt.order_by(Request.created_at.desc())
        .limit(PAGE_SIZE)
        .offset(page * PAGE_SIZE)
    )
    q = await session.execute(stmt)
    reqs = q.scalars().all()

    if not reqs:
        empty_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖️ Сбросить фильтры", callback_data="disp_filter:0:all:all")
        ]])
        return "🔎 По выбранным фильтрам заявок нет.", empty_kb

    localized_descriptions = await localize_request_descriptions(
        session, reqs, language, commit_immediately=True
    )

    # Build compact text
    status_name = "Все статусы" if status == "all" else STATUS_LABELS.get(status, status)
    category_name = "Все категории" if category == "all" else CATEGORY_LABELS.get(category, category)
    lines = [
        f"📋 <b>Заявки</b> — {page+1}/{total_pages} • всего {total}",
        f"🔎 {status_name} • {category_name}",
        "Нажмите на номер, чтобы открыть карточку\n",
    ]
    for req in reqs:
        resident = req.resident
        resident_str = (
            f"кв.{escape(resident.apartment or '?')} {escape(resident.full_name or '')}"
            if resident else "?"
        )
        # worker short
        w_str = ""
        if req.worker:
            w_str = f" → {escape(req.worker.full_name or str(req.worker.telegram_id))}"
        description = localized_descriptions[req.id]
        desc = format_description_html(
            description, language, compact=True, limit=60
        )
        date = format_local(req.created_at, "%d.%m %H:%M", "")
        lines.append(
            f"<b>#{req.id}</b> {CATEGORY_LABELS.get(req.category, req.category)} {STATUS_LABELS.get(req.status, req.status)}{w_str}\n"
            f"{resident_str} • {date}\n"
            f"{desc}\n"
        )

    text = "\n".join(lines)

    # Keyboard: one "📄 #ID" per request + pagination
    kb_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for req in reqs:
        btn = InlineKeyboardButton(
            text=f"📄 #{req.id}",
            callback_data=DispatcherFilteredRequestCallback(
                request_id=req.id, page=page, status=status, category=category
            ).pack(),
        )
        row.append(btn)
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    if total_pages > 1:
        pag_row: list[InlineKeyboardButton] = []
        if page > 0:
            pag_row.append(InlineKeyboardButton(text="◀️", callback_data=f"disp_filter:{page-1}:{status}:{category}"))
        pag_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pag_row.append(InlineKeyboardButton(text="▶️", callback_data=f"disp_filter:{page+1}:{status}:{category}"))
        kb_rows.append(pag_row)

    kb_rows.append([
        InlineKeyboardButton(
            text=("✅ " if status == "all" else "") + "Все",
            callback_data=f"disp_filter:0:all:{category}",
        ),
        InlineKeyboardButton(
            text=("✅ " if status == "new" else "") + "Новые",
            callback_data=f"disp_filter:0:new:{category}",
        ),
    ])
    kb_rows.append([
        InlineKeyboardButton(
            text=("✅ " if status == "accepted" else "") + "В работе",
            callback_data=f"disp_filter:0:accepted:{category}",
        ),
        InlineKeyboardButton(
            text=("✅ " if status == "closed" else "") + "Завершённые",
            callback_data=f"disp_filter:0:closed:{category}",
        ),
    ])
    category_buttons = []
    for category_code, label in CATEGORY_LABELS.items():
        category_buttons.append(InlineKeyboardButton(
            text=("✅ " if category == category_code else "") + label,
            callback_data=f"disp_filter:0:{status}:{category_code}",
        ))
    kb_rows.extend([[button] for button in category_buttons])
    if status != "all" or category != "all":
        kb_rows.append([InlineKeyboardButton(
            text="✖️ Сбросить фильтры", callback_data="disp_filter:0:all:all"
        )])

    await session.commit()
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


async def build_request_detail(
    session: AsyncSession,
    request_id: int,
    page: int,
    *,
    can_delete: bool = False,
    language: str | None = "ru",
) -> tuple[str, InlineKeyboardMarkup]:
    q = await session.execute(
        select(Request)
        .options(selectinload(Request.attachments))
        .where(Request.id == request_id)
    )
    req = q.scalar_one_or_none()
    if not req:
        return "Заявка не найдена.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"disp_list:{page}")]])

    rres = await session.execute(select(User).where(User.id == req.resident_id))
    resident = rres.scalar_one_or_none()
    w = None
    if req.worker_id:
        wres = await session.execute(select(User).where(User.id == req.worker_id))
        w = wres.scalar_one_or_none()

    description = await localize_request_description(
        session, req, language, commit_immediately=True
    )
    description_html = format_description_html(description, language)
    await session.commit()

    text = (
        f"🧾 <b>Заявка #{req.id}</b>\n"
        f"{CATEGORY_LABELS.get(req.category, req.category)} • {STATUS_LABELS.get(req.status, req.status)}\n"
        f"{URGENCY_LABELS.get(req.urgency, req.urgency)} приоритет\n\n"
        f"👤 <b>Житель:</b> {escape(resident.full_name or '') if resident else '?'}"
        f" • кв. {escape(resident.apartment or '?') if resident else '?'}\n"
        f"🧰 <b>Исполнитель:</b> {escape(w.full_name or str(w.telegram_id)) if w else 'не назначен'}\n\n"
        f"{description_html}\n\n"
        f"{'📍 <b>Место:</b> внутри квартиры' + chr(10) if req.service_area == 'apartment' else ''}"
        f"{'📍 <b>Место:</b> МОП / общее имущество' + chr(10) if req.service_area == 'common' else ''}"
        f"{'📎 <b>Вложений:</b> ' + str(len(req.attachments)) + chr(10) if req.attachments else ''}"
        f"🕒 Создана: {format_local(req.created_at, '%d.%m.%Y в %H:%M')}\n"
        f"{('▶️ Принята: ' + format_local(req.accepted_at, '%d.%m.%Y в %H:%M')) if req.accepted_at else ''}\n"
        f"{('✅ Завершена: ' + format_local(req.closed_at, '%d.%m.%Y в %H:%M')) if req.closed_at else ''}"
    )

    llm_flag = " ✨ ИИ" if getattr(req, "llm_meta", None) else ""
    if llm_flag:
        text = text.replace("🧾 <b>", "✨ ИИ-триаж применён\n🧾 <b>", 1)
    if req.completion_result:
        result_label = "выполнена" if req.completion_result == "done" else "не выполнена"
        text += (
            f"\n\n📌 <b>Результат:</b> {result_label}\n"
            f"💬 <b>Комментарий исполнителя:</b> "
            f"{escape(req.completion_comment or '—')}"
        )
    if req.approval_status:
        approval_labels = {
            "pending": "⏳ ожидает согласования председателя",
            "approved": "✅ согласована председателем",
            "rejected": "❌ отклонена председателем",
        }
        text += f"\n\n<b>Согласование:</b> {approval_labels[req.approval_status]}"
        if req.approval_comment:
            text += f"\n<b>Комментарий:</b> {escape(req.approval_comment)}"
    # Start from existing dispatcher_request_keyboard but add back button
    base_kb = dispatcher_request_keyboard(
        req.id,
        req.status,
        can_delete=can_delete,
        can_assign=req.category != "cleaning" and req.approval_status != "pending",
    )
    # base_kb has rows of 1 button each (or 2), we keep them
    rows = [list(row) for row in base_kb.inline_keyboard]
    if can_delete and req.approval_status == "pending":
        rows.insert(0, [
            InlineKeyboardButton(
                text="✅ Согласовать", callback_data=f"request_approve:{req.id}"
            ),
            InlineKeyboardButton(
                text="❌ Отклонить", callback_data=f"request_reject:{req.id}"
            ),
        ])
    # insert ✨ row after action rows, before back
    if req.category not in {"cleaning", "kazakhdomofon"}:
        rows.insert(len(rows), [InlineKeyboardButton(text="✨ ИИ-триаж", callback_data=f"ai_triage:{req.id}")])
    rows.append([
        InlineKeyboardButton(
            text="🕓 История",
            callback_data=DispatcherHistoryCallback(
                request_id=req.id, page=page
            ).pack(),
        )
    ])
    # add back row
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data=f"disp_list:{page}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_request_attachments(
    bot: Bot, session: AsyncSession, chat_id: int, request_id: int
) -> None:
    result = await session.execute(
        select(Request)
        .options(selectinload(Request.attachments))
        .where(Request.id == request_id)
    )
    request = result.scalar_one_or_none()
    if not request:
        return
    for attachment in request.attachments:
        try:
            if attachment.media_type == "photo":
                await bot.send_photo(chat_id, attachment.file_id)
            elif attachment.media_type == "video":
                await bot.send_video(chat_id, attachment.file_id)
            else:
                await bot.send_document(chat_id, attachment.file_id)
        except Exception:
            logger.exception(
                "request_attachment_delivery_failed request_id=%s", request_id
            )


@router.message(F.text.in_(text_variants("summary")))
async def dispatcher_summary(message: Message, session: AsyncSession):
    user = await get_actor(session, message.from_user.id)
    if not user or not _is_dispatcher(user):
        await message.answer("Только для диспетчеров.")
        return

    text, keyboard = await build_overview(session, ReportFilters())
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


def _report_filters(callback_data: ReportCallback) -> ReportFilters:
    def decode(mapping: dict[str, str], value: str) -> str:
        return next((key for key, code in mapping.items() if code == value), value)

    return ReportFilters(
        period=decode({"today": "t", "yesterday": "y", "7d": "7", "30d": "30", "month": "m", "prev_month": "pm", "custom": "c"}, callback_data.period),
        category=decode({
            "all": "a", "electrician": "e", "plumber": "p",
            "security": "s", "cleaning": "c", "kazakhdomofon": "k",
        }, callback_data.category),
        urgency=decode({"all": "a", "high": "h", "normal": "n", "low": "l", "none": "x"}, callback_data.urgency),
        worker_id=callback_data.worker_id,
        result=decode({"all": "a", "done": "d", "not_done": "n", "none": "x"}, callback_data.result),
        escalation=decode({"all": "a", "yes": "y", "no": "n"}, callback_data.escalation),
        custom_start=date.fromordinal(callback_data.start_day) if callback_data.start_day else None,
        custom_end=date.fromordinal(callback_data.end_day) if callback_data.end_day else None,
    )


async def _edit_report(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@router.callback_query(ReportCallback.filter())
async def dispatcher_report_callback(
    callback: CallbackQuery,
    callback_data: ReportCallback,
    session: AsyncSession,
    state: FSMContext,
):
    user = await get_actor(session, callback.from_user.id)
    if not is_dispatcher(user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    filters = _report_filters(callback_data)
    action = callback_data.action
    if action == "p":
        if filters.period == "custom":
            await state.set_state(ReportDateStates.waiting_start)
            await callback.message.answer(
                "📅 Введите начало периода в формате <b>ДД.ММ.ГГГГ</b>\nНапример: 01.03.2026",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(user.language),
            )
            await callback.answer()
            return
        action = "o"
    if action == "x":
        await callback.message.answer_document(
            await export_csv(session, filters, language=user.language),
            caption="Отчёт готов",
        )
        await callback.answer()
        return
    if action == "f":
        text, keyboard = await build_filter_screen(session, filters)
    elif action in {"c", "w"}:
        text, keyboard = await build_breakdown(session, filters, "workers" if action == "w" else "categories")
    elif action == "d":
        text, keyboard = await build_dynamics(session, filters)
    elif action == "fc":
        choices = [("Все категории", "all"), *[(CATEGORY_LABELS[c], c) for c in REQUEST_CATEGORIES]]
        text, keyboard = "🗂 <b>Выберите категорию</b>", choice_keyboard(filters, "category", choices)
    elif action == "fu":
        choices = [("Все приоритеты", "all"), ("Высокий", "high"), ("Обычный", "normal"), ("Низкий", "low"), ("Не определён", "none")]
        text, keyboard = "🔥 <b>Выберите приоритет</b>", choice_keyboard(filters, "urgency", choices)
    elif action == "fw":
        text, keyboard = "👷 <b>Выберите исполнителя</b>", choice_keyboard(filters, "worker_id", await worker_choices(session))
    elif action == "fr":
        choices = [("Любой результат", "all"), ("Выполнено", "done"), ("Не выполнено", "not_done"), ("Без результата", "none")]
        text, keyboard = "🎯 <b>Выберите результат</b>", choice_keyboard(filters, "result", choices)
    elif action == "fe":
        choices = [("Любая", "all"), ("Эскалированные", "yes"), ("Без эскалации", "no")]
        text, keyboard = "🚨 <b>Выберите эскалацию</b>", choice_keyboard(filters, "escalation", choices)
    else:
        text, keyboard = await build_overview(session, filters)
    await _edit_report(callback, text, keyboard)
    await callback.answer()


def _parse_report_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


@router.message(ReportDateStates.waiting_start)
async def report_custom_start(message: Message, state: FSMContext):
    start = _parse_report_date(message.text or "")
    if not start:
        await message.answer("Не удалось распознать дату. Введите её как ДД.ММ.ГГГГ.")
        return
    await state.update_data(report_start=start.isoformat())
    await state.set_state(ReportDateStates.waiting_end)
    await message.answer("Введите конец периода в формате <b>ДД.ММ.ГГГГ</b>.", parse_mode="HTML")


@router.message(ReportDateStates.waiting_end)
async def report_custom_end(message: Message, session: AsyncSession, state: FSMContext):
    end = _parse_report_date(message.text or "")
    data = await state.get_data()
    start = date.fromisoformat(data["report_start"])
    if not end:
        await message.answer("Не удалось распознать дату. Введите её как ДД.ММ.ГГГГ.")
        return
    if end < start:
        await message.answer("Конец периода не может быть раньше начала.")
        return
    if (end - start).days > 730:
        await message.answer("Период не может превышать два года.")
        return
    await state.clear()
    text, keyboard = await build_overview(
        session, ReportFilters(period="custom", custom_start=start, custom_end=end)
    )
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(F.text.in_(text_variants("all_requests")))
async def all_requests(message: Message, session: AsyncSession):
    user = await get_actor(session, message.from_user.id)
    if not user or not _is_dispatcher(user):
        await message.answer("Только для диспетчеров.")
        return
    text, kb = await build_dispatcher_list(
        session, page=0, language=user.language
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("disp_list:"))
async def disp_list(callback: CallbackQuery, session: AsyncSession):
    # also check auth
    user = await get_actor(session, callback.from_user.id)
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    page = int(callback.data.split(":")[1])
    text, kb = await build_dispatcher_list(session, page, language=user.language)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        # if not modified, just answer
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("disp_filter:"))
async def disp_filter(callback: CallbackQuery, session: AsyncSession):
    user = await get_actor(session, callback.from_user.id)
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    try:
        _, raw_page, status, category = callback.data.split(":", 3)
        page = max(0, int(raw_page))
    except (ValueError, AttributeError):
        await callback.answer("Некорректный фильтр", show_alert=True)
        return
    if status not in {"all", "new", "accepted", "closed"}:
        status = "all"
    if category not in {"all", *REQUEST_CATEGORIES}:
        category = "all"
    text, kb = await build_dispatcher_list(
        session, page, status, category, language=user.language
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.answer("Фильтр уже выбран")
        return
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(DispatcherRequestCallback.filter())
async def req_view(
    callback: CallbackQuery,
    callback_data: DispatcherRequestCallback,
    session: AsyncSession,
    bot: Bot,
):
    req_id = callback_data.request_id
    page = callback_data.page
    user = await get_actor(session, callback.from_user.id)
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_request_detail(
        session,
        req_id,
        page,
        can_delete=is_administrator(user),
        language=user.language,
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await _send_request_attachments(bot, session, callback.from_user.id, req_id)
    await callback.answer()


@router.callback_query(DispatcherFilteredRequestCallback.filter())
async def filtered_req_view(
    callback: CallbackQuery,
    callback_data: DispatcherFilteredRequestCallback,
    session: AsyncSession,
    bot: Bot,
):
    user = await get_actor(session, callback.from_user.id)
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_request_detail(
        session,
        callback_data.request_id,
        callback_data.page,
        can_delete=is_administrator(user),
        language=user.language,
    )
    rows = [list(row) for row in kb.inline_keyboard]
    rows[-1] = [InlineKeyboardButton(
        text="◀️ К списку",
        callback_data=(
            f"disp_filter:{callback_data.page}:"
            f"{callback_data.status}:{callback_data.category}"
        ),
    )]
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await _send_request_attachments(
        bot, session, callback.from_user.id, callback_data.request_id
    )
    await callback.answer()


@router.callback_query(DispatcherHistoryCallback.filter())
async def request_history(
    callback: CallbackQuery,
    callback_data: DispatcherHistoryCallback,
    session: AsyncSession,
):
    viewer = await get_actor(session, callback.from_user.id)
    if not _is_dispatcher(viewer):
        await callback.answer("Нет прав", show_alert=True)
        return

    result = await session.execute(
        select(RequestEvent)
        .options(selectinload(RequestEvent.actor))
        .where(RequestEvent.request_id == callback_data.request_id)
        .order_by(RequestEvent.created_at, RequestEvent.id)
    )
    events = result.scalars().all()
    action_labels = {
        "created": "создана",
        "approved": "согласована председателем",
        "rejected": "отклонена председателем",
        "claimed": "принята исполнителем",
        "assigned": "назначена",
        "reassigned": "переназначена",
        "closed": "завершена",
        "deleted": "удалена",
    }
    lines = [f"🕓 <b>История заявки #{callback_data.request_id}</b>"]
    if not events:
        lines.append("История пока отсутствует.")
    for event in events:
        timestamp = format_local(event.created_at, "%d.%m.%Y %H:%M")
        actor = "система"
        if event.actor:
            actor = event.actor.full_name or str(event.actor.telegram_id)
        detail_values: list[str] = []
        for item in (event.details or "").split(";"):
            key, separator, value = item.partition("=")
            if not separator:
                continue
            if key == "category":
                detail_values.append(
                    f"категория: {category_label(value, viewer.language)}"
                )
            elif key == "completion_result":
                result = {
                    "done": "выполнено", "not_done": "не выполнено",
                }.get(value, "неизвестно")
                detail_values.append(f"результат: {result}")
            elif key == "worker_id":
                detail_values.append(f"исполнитель: №{value}")
            elif key == "previous_worker_id":
                detail_values.append(f"предыдущий исполнитель: №{value}")
            elif key == "by":
                source = {
                    "administrator": "председатель", "resident": "житель",
                }.get(value, "неизвестно")
                detail_values.append(f"удалил: {source}")
            elif key == "demo":
                detail_values.append(
                    f"демо-данные: {'да' if value == 'true' else 'нет'}"
                )
        detail = (
            f" — {escape('; '.join(detail_values))}" if detail_values else ""
        )
        lines.append(
            f"• {timestamp}: {action_labels.get(event.action, escape(event.action))} "
            f"({escape(actor)}){detail}"
        )

    back = DispatcherRequestCallback(
        request_id=callback_data.request_id,
        page=callback_data.page,
    ).pack()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ К заявке", callback_data=back)]]
    )
    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=kb
    )
    await callback.answer()


# keep old paginate prefix for backwards compat if any old messages remain
@router.callback_query(F.data.startswith("disp_page:"))
async def paginate_compat(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    user = await get_actor(session, callback.from_user.id)
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_dispatcher_list(session, page, language=user.language)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("assign:") | F.data.startswith("reassign:"))
async def start_assign(callback: CallbackQuery, session: AsyncSession):
    request_id = int(callback.data.split(":")[1])
    user = await get_actor(session, callback.from_user.id)
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if req.category == "cleaning":
        await callback.answer(
            "Заявку клининга принимает сам клининг", show_alert=True
        )
        return
    if req.approval_status == "pending":
        await callback.answer("Сначала согласуйте заявку", show_alert=True)
        return
    wres = await session.execute(select(User).where(User.role == "worker", User.worker_category == req.category, User.is_approved.is_(True)))
    workers = wres.scalars().all()
    if not workers:
        await callback.answer(f"Нет исполнителей категории {CATEGORY_LABELS.get(req.category)}", show_alert=True)
        return
    await callback.message.answer(
        f"Выберите исполнителя для заявки #{req.id} ({CATEGORY_LABELS.get(req.category)}):",
        reply_markup=assign_worker_keyboard(req.id, workers)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do_assign:"))
async def do_assign(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    _, req_id, worker_db_id = callback.data.split(":")
    request_id = int(req_id)
    worker_db_id = int(worker_db_id)

    user = await get_actor(session, callback.from_user.id)
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return

    success, msg = await assign_request(
        session, request_id, worker_db_id, actor=user
    )
    if not success:
        await callback.answer(msg, show_alert=True)
        return
    await session.commit()
    await callback.message.edit_text(callback.message.text + "\n\n✅ Назначен")
    await callback.answer("Исполнитель назначен")

    wres = await session.execute(select(User).where(User.id == worker_db_id))
    worker = wres.scalar_one_or_none()
    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if worker and req:
        try:
            localized = await localize_request_description(
                session, req, worker.language, commit_immediately=True
            )
            description_html = format_description_html(
                localized, worker.language
            )
            await session.commit()
            await send_to_user(
                bot, session, worker,
                f"📌 Вам назначена заявка #{req.id}\n\n{description_html}",
            )
        except Exception:
            pass
        rres = await session.execute(select(User).where(User.id == req.resident_id))
        resident = rres.scalar_one_or_none()
        if resident:
            try:
                await send_to_user(
                    bot, session, resident,
                    f"📌 Ваша заявка #{req.id} назначена исполнителю "
                    f"{escape(worker.full_name or str(worker.telegram_id))}",
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("cancel_assign:"))
async def cancel_assign(callback: CallbackQuery):
    await callback.message.edit_text("Назначение отменено")
    await callback.answer()


PEND_PAGE_SIZE = 5

async def build_pending_list(
    session: AsyncSession,
    page: int,
    language: str | None = None,
    *,
    include_request_approvals: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    pending_filter = (
        User.is_approved.is_(False),
        ~((User.role == "resident") & (User.resident_subrole == "tenant")),
    )
    registrations = select(
        literal("registration").label("kind"),
        User.id.label("item_id"),
        User.created_at.label("created_at"),
    ).where(*pending_filter)
    if include_request_approvals:
        request_approvals = select(
            literal("request").label("kind"),
            Request.id.label("item_id"),
            Request.created_at.label("created_at"),
        ).where(
            Request.status == "new",
            Request.approval_status == "pending",
        )
        pending_items = registrations.union_all(request_approvals).subquery()
    else:
        pending_items = registrations.subquery()
    total_res = await session.execute(
        select(func.count()).select_from(pending_items)
    )
    total = total_res.scalar() or 0
    total_pages = (total + PEND_PAGE_SIZE - 1) // PEND_PAGE_SIZE if total else 1
    page = max(0, min(page, total_pages - 1))
    result = await session.execute(
        select(
            pending_items.c.kind,
            pending_items.c.item_id,
            pending_items.c.created_at,
        )
        .order_by(pending_items.c.created_at.desc(), pending_items.c.item_id.desc())
        .limit(PEND_PAGE_SIZE)
        .offset(page * PEND_PAGE_SIZE)
    )
    items = result.all()
    if not items:
        return t(
            "pending_items_empty", language
        ), InlineKeyboardMarkup(inline_keyboard=[])

    user_ids = [item.item_id for item in items if item.kind == "registration"]
    request_ids = [item.item_id for item in items if item.kind == "request"]
    users: dict[int, User] = {}
    requests: dict[int, Request] = {}
    if user_ids:
        user_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        users = {user.id: user for user in user_result.scalars()}
    if request_ids:
        request_result = await session.execute(
            select(Request)
            .options(selectinload(Request.resident))
            .where(Request.id.in_(request_ids))
        )
        requests = {request.id: request for request in request_result.scalars()}
    localized_descriptions = await localize_request_descriptions(
        session,
        list(requests.values()),
        language,
        commit_immediately=True,
    )

    lines = [t(
        "pending_items_heading",
        language,
        page=page + 1,
        pages=total_pages,
        total=total,
    )]
    for item in items:
        if item.kind == "registration":
            user = users[item.item_id]
            public_role = role_label(user.role, language)
            worker_category = (
                f" ({category_label(user.worker_category, language)})"
                if user.worker_category else ""
            )
            lines.append(t(
                "pending_registration_item",
                language,
                id=user.id,
                role=f"{public_role}{worker_category}",
                created=format_local(user.created_at, "%d.%m %H:%M", ""),
                name=escape(user.full_name or "—"),
                apartment=escape(user.apartment or "—"),
            ))
        else:
            request = requests[item.item_id]
            resident = request.resident
            localized = localized_descriptions[request.id]
            description = format_description_html(
                localized, language, compact=True, limit=80
            )
            lines.append(t(
                "pending_request_item",
                language,
                id=request.id,
                created=format_local(request.created_at, "%d.%m %H:%M", ""),
                resident=escape(resident.full_name or "—") if resident else "—",
                apartment=escape(resident.apartment or "—") if resident else "—",
                description=description,
            ))
    text = "\n".join(lines)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for item in items:
        if item.kind == "registration":
            button = InlineKeyboardButton(
                text=f"👤 #{item.item_id}",
                callback_data=f"pend_view:{item.item_id}:{page}",
            )
        else:
            button = InlineKeyboardButton(
                text=f"📹 #{item.item_id}",
                callback_data=f"pend_req_view:{item.item_id}:{page}",
            )
        row.append(button)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if total_pages > 1:
        pag: list[InlineKeyboardButton] = []
        if page > 0:
            pag.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"pend_list:{page-1}"))
        pag.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pag.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"pend_list:{page+1}"))
        rows.append(pag)
    await session.commit()
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

async def build_pending_detail(
    session: AsyncSession, user_id: int, page: int, language: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    res = await session.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if not u:
        return "Не найден.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"pend_list:{page}")]])
    public_role = role_label(u.role, language)
    text = (
        f"{t('pending_registration_detail', language, role=public_role)}\n"
        f"ID: {u.id} | TG: <code>{u.telegram_id}</code>\n"
        f"Имя: {escape(u.full_name or '—')} | Кв: {escape(u.apartment or '—')}\n"
        f"Категория: {category_label(u.worker_category, language) or '—'}\n"
        f"Роль: {public_role} | Одобрен: {t('yes' if u.is_approved else 'no', language)}\n"
        f"Создан: {format_local(u.created_at, '%d.%m %H:%M')}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{u.id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{u.id}")],
        [InlineKeyboardButton(text="◀️ К списку", callback_data=f"pend_list:{page}")],
    ])
    return text, kb

# --- Action queue: registrations plus chairman-only request approvals ---

@router.message(F.text.in_(
    text_variants("pending_workers")
    | {
        "⏳ На подтверждение",
        "⏳ Растауға",
        "👤 Новые регистрации",
        "👤 Жаңа тіркеулер",
    }
))
async def pending_approvals(message: Message, session: AsyncSession):
    viewer = await get_actor(session, message.from_user.id)
    if not viewer or not _is_dispatcher(viewer):
        await message.answer("Только для диспетчеров.")
        return
    text, kb = await build_pending_list(
        session,
        page=0,
        language=viewer.language,
        include_request_approvals=is_administrator(viewer),
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("pend_list:"))
async def pend_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    viewer = await get_actor(session, callback.from_user.id)
    if not viewer or not _is_dispatcher(viewer):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_pending_list(
        session,
        page,
        language=viewer.language,
        include_request_approvals=is_administrator(viewer),
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("pend_view:"))
async def pend_view(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    uid = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    viewer = await get_actor(session, callback.from_user.id)
    if not viewer or not _is_dispatcher(viewer):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_pending_detail(
        session, uid, page, language=viewer.language
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("pend_req_view:"))
async def pending_request_view(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
):
    parts = callback.data.split(":")
    request_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    viewer = await get_actor(session, callback.from_user.id)
    if not is_administrator(viewer):
        await callback.answer("Только для председателя", show_alert=True)
        return
    text, kb = await build_request_detail(
        session,
        request_id,
        page,
        can_delete=True,
        language=viewer.language,
    )
    rows = [list(row) for row in kb.inline_keyboard]
    rows[-1] = [InlineKeyboardButton(
        text="◀️ К списку", callback_data=f"pend_list:{page}"
    )]
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await _send_request_attachments(
        bot, session, callback.from_user.id, request_id
    )
    await callback.answer()


@router.callback_query(F.data.startswith("approve:"))
async def approve_user(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not _is_dispatcher(await get_actor(session, callback.from_user.id)):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_db_id = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.id == user_db_id))
    u = result.scalar_one_or_none()
    if not u:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    if u.role == "resident" and u.resident_subrole == "tenant":
        await callback.answer("Арендатора подтверждает собственник", show_alert=True)
        return
    if u.is_approved:
        await callback.answer("Уже одобрен", show_alert=True)
        return
    u.is_approved = True
    if u.role == "resident" and u.resident_subrole is None:
        u.resident_subrole = "owner"
    await session.commit()
    await callback.message.edit_text(callback.message.text + "\n\n✅ Одобрен")
    await callback.answer("Одобрен")
    try:
        await send_to_user(
            bot, session, u,
            "✅ Ваша регистрация подтверждена диспетчером! Теперь можете создать заявку: /start",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("reject:"))
async def reject_user(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not _is_dispatcher(await get_actor(session, callback.from_user.id)):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_db_id = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.id == user_db_id))
    u = result.scalar_one_or_none()
    if not u:
        await callback.answer("Не найден", show_alert=True)
        return
    if u.role == "resident" and u.resident_subrole == "tenant":
        await callback.answer("Арендатора подтверждает собственник", show_alert=True)
        return
    tid = await delivery_telegram_id(session, u)
    await session.delete(u)
    await session.commit()
    await callback.message.edit_text(callback.message.text + "\n\n❌ Отклонен")
    await callback.answer("Отклонен")
    try:
        if tid is not None:
            await bot.send_message(tid, "❌ Ваша регистрация отклонена диспетчером. Обратитесь в диспетчерскую.")
    except Exception:
        pass


# Announcements

@router.message(F.text.in_(text_variants("announcement")))
async def create_ann_start(message: Message, state: FSMContext, session: AsyncSession):
    user = await get_actor(session, message.from_user.id)
    if not user or not _is_dispatcher(user):
        await message.answer("Только для диспетчеров.")
        return
    await message.answer("Введите текст объявления для всех жителей:", reply_markup=cancel_keyboard())
    await state.set_state(AnnouncementStates.waiting_text)


@router.message(AnnouncementStates.waiting_text, F.text)
async def create_ann_finish(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    text = message.text.strip()
    if len(text) < 10:
        await message.answer("Текст слишком короткий (минимум 10 символов):")
        return
    # LLM polish (opt-in): suggest improved version for dispatcher to confirm
    try:
        llm = get_llm()
        if llm.enabled:
            res = await llm.polish(text)
            polished = res.text.strip()
            # off_topic => the draft is not a resident announcement; don't suggest.
            if not res.off_topic and polished and polished != text and len(polished) >= 10:
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                await state.update_data(pending_ann_raw=text, pending_ann_polished=polished)
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✨ Использовать улучшенный", callback_data="ann_use:polished")],
                    [InlineKeyboardButton(text="📝 Оставить как есть", callback_data="ann_use:raw")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
                ])
                await message.answer(
                    f"✨ Предлагаю улучшенный текст:\n\n<i>{escape(polished[:800])}</i>\n\n"
                    f"Оригинал: <i>{escape(text[:400])}</i>",
                    parse_mode="HTML", reply_markup=kb
                )
                return
    except Exception:
        logger.exception("announcement_polish_failed")

    user = await get_actor(session, message.from_user.id)
    ann = await create_announcement(session, author_id=user.id, text=text)
    await session.commit()
    await state.clear()
    await message.answer(f"✅ Объявление #{ann.id} создано, рассылаю...")
    report = await broadcast_announcement(bot, session, text)
    await message.answer(
        f"📢 Рассылка завершена. Доставлено: {report.delivered}, ошибок: {report.failed}."
    )


@router.callback_query(F.data.startswith("ann_use:"))
async def ann_use_pick(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()
    text = data.get("pending_ann_polished") if choice == "polished" else data.get("pending_ann_raw")
    if not text:
        await callback.answer("Истёк таймаут, введите заново", show_alert=True)
        await state.clear()
        return
    user = await get_actor(session, callback.from_user.id)
    ann = await create_announcement(session, author_id=user.id, text=text)
    await session.commit()
    await state.clear()
    await callback.message.edit_text(f"✅ Объявление #{ann.id} создано ({'✨ ИИ' if choice=='polished' else 'оригинал'}), рассылаю...")
    report = await broadcast_announcement(bot, session, text)
    try:
        await callback.message.answer(
            f"📢 Рассылка завершена. Доставлено: {report.delivered}, ошибок: {report.failed}."
        )
    except Exception:
        pass
    await callback.answer()



# LLM triage for dispatcher on demand

@router.callback_query(F.data.startswith("ai_triage:"))
async def ai_triage(callback: CallbackQuery, session: AsyncSession):
    if not _is_dispatcher(await get_actor(session, callback.from_user.id)):
        await callback.answer("Нет прав", show_alert=True)
        return
    req_id = int(callback.data.split(":", 1)[1])
    from sqlalchemy import select as _sel
    from bot.models import Request as Req
    res = await session.execute(_sel(Req).where(Req.id == req_id))
    req = res.scalar_one_or_none()
    if not req:
        await callback.answer("Не найдена", show_alert=True)
        return
    llm = get_llm()
    if not llm.enabled:
        await callback.answer("ИИ отключен (нет ключа)", show_alert=True)
        return
    try:
        await callback.answer("✨ Анализирую...")
        tri = await llm.triage(req.description, req.category)
        await callback.message.answer(
            f"✨ <b>ИИ-триаж #{req.id}</b>\nПриоритет: "
            f"<b>{escape(URGENCY_LABELS.get(tri.priority, 'Не определён'))}</b>\n"
            f"Кратко: {escape(tri.summary)}\nПодсказка: {escape(tri.hint)}",
            parse_mode="HTML"
        )
    except Exception:
        logger.exception("request_triage_failed request_id=%s", req.id)
        await callback.message.answer("⚠️ ИИ временно недоступен. Попробуйте позже.")


# Add worker

def _schedule_action_keyboard(worker_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("schedule_add_hours", language), callback_data=f"schedule_hours:{worker_id}")],
        [InlineKeyboardButton(text=t("schedule_absence", language), callback_data=f"schedule_unavailable:{worker_id}"),
         InlineKeyboardButton(text=t("schedule_extra_shift", language), callback_data=f"schedule_available:{worker_id}")],
        [InlineKeyboardButton(text=t("schedule_clear", language), callback_data=f"schedule_clear:{worker_id}")],
    ])


@router.message(F.text.in_(text_variants("worker_schedules")))
async def schedules_start(message: Message, session: AsyncSession):
    if not await _require_dispatcher(message, session):
        return
    language = await _event_language(message, session)
    result = await session.execute(
        select(User).where(User.role == "worker", User.is_approved.is_(True)).order_by(User.full_name)
    )
    workers = list(result.scalars().all())
    if not workers:
        await message.answer(t("no_approved_workers", language))
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{worker.full_name or worker.telegram_id} · {category_label(worker.worker_category, language)}",
            callback_data=f"schedule_view:{worker.id}",
        )]
        for worker in workers
    ])
    await message.answer(
        f"🗓 {t('worker_schedules', language)}\n{t('organization_timezone', language)}: "
        f"{DISPLAY_TIMEZONE}\n\n{t('choose_worker', language)}",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("schedule_view:"))
async def schedule_view(callback: CallbackQuery, session: AsyncSession):
    if not await _require_dispatcher(callback, session):
        return
    worker_id = int(callback.data.split(":", 1)[1])
    language = await _event_language(callback, session)
    worker = await session.get(User, worker_id)
    if worker is None or worker.role != "worker":
        await callback.answer(t("worker_not_found", language), show_alert=True)
        return
    hours_result = await session.execute(
        select(WorkerWorkingHour)
        .where(WorkerWorkingHour.worker_id == worker_id)
        .order_by(WorkerWorkingHour.weekday, WorkerWorkingHour.start_time)
    )
    hours = list(hours_result.scalars().all())
    exception_result = await session.execute(
        select(WorkerScheduleException)
        .where(WorkerScheduleException.worker_id == worker_id)
        .order_by(WorkerScheduleException.starts_at.desc())
        .limit(5)
    )
    exceptions = list(exception_result.scalars().all())
    lines = [f"🗓 <b>{escape(worker.full_name or str(worker.telegram_id))}</b>"]
    lines.append(
        f"{t('actually_on_shift', language)}: "
        f"{t('yes', language) if worker.is_on_shift else t('no', language)}"
    )
    lines.append(f"\n<b>{t('recurring_hours', language)}</b>")
    if hours:
        lines.extend(
            f"• {WEEKDAY_LABELS[item.weekday]} {item.start_time.strftime('%H:%M')}–{item.end_time.strftime('%H:%M')}"
            for item in hours
        )
    else:
        lines.append(t("schedule_not_set", language))
    if exceptions:
        lines.append(f"\n<b>{t('recent_schedule_exceptions', language)}</b>")
        for item in exceptions:
            kind = t("schedule_available", language) if item.is_available else t("schedule_unavailable", language)
            lines.append(
                f"• {format_local(item.starts_at, '%d.%m %H:%M')}–{format_local(item.ends_at, '%d.%m %H:%M')} "
                f"{kind}{' · ' + escape(item.reason) if item.reason else ''}"
            )
    await callback.message.answer("\n".join(lines), reply_markup=_schedule_action_keyboard(worker_id, language))
    await callback.answer()


@router.callback_query(F.data.startswith("schedule_hours:"))
async def schedule_hours_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _require_dispatcher(callback, session):
        return
    worker_id = int(callback.data.split(":", 1)[1])
    language = await _event_language(callback, session)
    await state.set_state(ScheduleStates.waiting_hours)
    await state.update_data(schedule_worker_id=worker_id)
    await callback.message.answer(
        t("schedule_hours_prompt", language),
        reply_markup=reply_cancel_keyboard(language),
    )
    await callback.answer()


@router.message(ScheduleStates.waiting_hours)
async def schedule_hours_save(message: Message, state: FSMContext, session: AsyncSession):
    language = await _event_language(message, session)
    try:
        weekdays, starts, ends = parse_recurring_hours(message.text or "", language)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    data = await state.get_data()
    worker_id = int(data["schedule_worker_id"])
    await add_recurring_hours(session, worker_id, weekdays, starts, ends)
    await session.commit()
    await state.clear()
    await message.answer(t("schedule_hours_added", language))


@router.callback_query(F.data.startswith("schedule_unavailable:") | F.data.startswith("schedule_available:"))
async def schedule_exception_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _require_dispatcher(callback, session):
        return
    action, raw_id = callback.data.split(":", 1)
    language = await _event_language(callback, session)
    await state.set_state(ScheduleStates.waiting_exception_details)
    await state.update_data(
        schedule_worker_id=int(raw_id),
        schedule_is_available=(action == "schedule_available"),
    )
    await callback.message.answer(
        t("schedule_exception_prompt", language),
        reply_markup=reply_cancel_keyboard(language),
    )
    await callback.answer()


@router.message(ScheduleStates.waiting_exception_details)
async def schedule_exception_save(message: Message, state: FSMContext, session: AsyncSession):
    language = await _event_language(message, session)
    try:
        starts, ends, reason = parse_local_exception(message.text or "", language)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    data = await state.get_data()
    await add_local_exception(
        session,
        int(data["schedule_worker_id"]),
        starts,
        ends,
        is_available=bool(data["schedule_is_available"]),
        reason=reason,
        language=language,
    )
    await session.commit()
    await state.clear()
    await message.answer(t("schedule_exception_added", language))


@router.callback_query(F.data.startswith("schedule_clear:"))
async def schedule_clear(callback: CallbackQuery, session: AsyncSession):
    if not await _require_dispatcher(callback, session):
        return
    worker_id = int(callback.data.split(":", 1)[1])
    language = await _event_language(callback, session)
    await clear_recurring_hours(session, worker_id)
    await session.commit()
    await callback.answer(t("schedule_cleared", language), show_alert=True)


@router.message(F.text.in_(text_variants("add_worker")))
async def add_worker_start(message: Message, state: FSMContext, session: AsyncSession):
    user = await get_actor(session, message.from_user.id)
    if not user or not _is_dispatcher(user):
        await message.answer("Только для диспетчеров.")
        return
    await message.answer("Введите Telegram ID исполнителя (число, можно узнать через @userinfobot):", reply_markup=cancel_keyboard())
    await state.set_state(AddWorkerStates.waiting_telegram_id)


@router.message(AddWorkerStates.waiting_telegram_id, F.text)
async def add_worker_tid(message: Message, state: FSMContext):
    tid_raw = message.text.strip()
    if not tid_raw.isdigit():
        await message.answer("Введите числовой Telegram ID:")
        return
    await state.update_data(worker_tid=int(tid_raw))
    await message.answer("Выберите категорию исполнителя:", reply_markup=category_keyboard("add_worker_cat"))
    # keep cancel: add extra row via followup message with cancel
    await message.answer("Нажмите ❌ Отмена чтобы отменить.", reply_markup=cancel_keyboard())
    await state.set_state(AddWorkerStates.waiting_category)


@router.callback_query(F.data.startswith("add_worker_cat:"))
async def add_worker_finish(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not _is_dispatcher(await get_actor(session, callback.from_user.id)):
        await callback.answer("Нет прав", show_alert=True)
        return
    category = callback.data.split(":")[1]
    data = await state.get_data()
    worker_tid = data.get("worker_tid")
    if not worker_tid:
        await callback.answer("Ошибка данных", show_alert=True)
        return

    result = await session.execute(select(User).where(User.telegram_id == worker_tid))
    existing = result.scalar_one_or_none()
    if existing:
        existing.role = "worker"
        existing.worker_category = category
        existing.is_approved = True
        existing.is_on_shift = False
    else:
        new_worker = User(telegram_id=worker_tid, role="worker", worker_category=category, is_approved=True, is_on_shift=False)
        session.add(new_worker)
    await session.commit()
    await state.clear()
    await callback.message.edit_text(f"✅ Исполнитель {worker_tid} добавлен как {CATEGORY_LABELS.get(category)}")
    await callback.answer()
