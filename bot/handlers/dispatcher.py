from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from html import escape
import logging

from bot.models import User, Request, RequestEvent
from bot.constants import URGENCY_LABELS, REQUEST_CATEGORIES
from bot.states import AnnouncementStates, AddWorkerStates
from bot.keyboards import (
    CATEGORY_LABELS, STATUS_LABELS,
    dispatcher_request_keyboard, assign_worker_keyboard, category_keyboard, approval_keyboard, cancel_keyboard, reply_cancel_keyboard
)
from bot.services.requests import assign_request, create_announcement
from bot.services.llm import get_llm
from bot.services.notify import broadcast_announcement
from bot.auth import is_dispatcher
from bot.callbacks import (
    DispatcherRequestCallback,
    DispatcherHistoryCallback,
    DispatcherFilteredRequestCallback,
)

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data == "cancel_fsm")
async def dispatcher_cancel_cb(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if await state.get_state() is None:
        await callback.answer()
        return
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    res = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    u = res.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    try:
        await callback.message.edit_text("❌ Отмена")
    except Exception:
        pass
    if kb:
        await callback.message.answer("Главное меню", reply_markup=kb)
    await callback.answer()

@router.message(F.text.in_({"❌ Отмена", "❌ Болдырмау"}))
async def dispatcher_cancel_text(message: Message, state: FSMContext, session: AsyncSession):
    if await state.get_state() is None:
        return
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    u = res.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await message.answer("❌ Отменено.", reply_markup=kb)

PAGE_SIZE = 5


def _is_dispatcher(user: User) -> bool:
    return is_dispatcher(user)


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
        desc = escape(req.description.strip().replace("\n", " "))
        if len(desc) > 60:
            desc = desc[:60] + "…"
        date = req.created_at.strftime("%d.%m %H:%M") if req.created_at else ""
        lines.append(
            f"<b>#{req.id}</b> {CATEGORY_LABELS.get(req.category, req.category)} {STATUS_LABELS.get(req.status, req.status)}{w_str}\n"
            f"{resident_str} • {date}\n"
            f"<i>{desc}</i>\n"
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
            text=("✅ " if status == "closed" else "") + "Закрытые",
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

    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


async def build_request_detail(session: AsyncSession, request_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if not req:
        return "Заявка не найдена.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"disp_list:{page}")]])

    rres = await session.execute(select(User).where(User.id == req.resident_id))
    resident = rres.scalar_one_or_none()
    w = None
    if req.worker_id:
        wres = await session.execute(select(User).where(User.id == req.worker_id))
        w = wres.scalar_one_or_none()

    text = (
        f"🧾 <b>Заявка #{req.id}</b>\n"
        f"{CATEGORY_LABELS.get(req.category, req.category)} • {STATUS_LABELS.get(req.status, req.status)}\n"
        f"{URGENCY_LABELS.get(req.urgency, req.urgency)} приоритет\n\n"
        f"👤 <b>Житель:</b> {escape(resident.full_name or '') if resident else '?'}"
        f" • кв. {escape(resident.apartment or '?') if resident else '?'}\n"
        f"🧰 <b>Исполнитель:</b> {escape(w.full_name or str(w.telegram_id)) if w else 'не назначен'}\n\n"
        f"📝 <b>Описание</b>\n{escape(req.description)}\n\n"
        f"🕒 Создана: {req.created_at.strftime('%d.%m.%Y в %H:%M') if req.created_at else '—'}\n"
        f"{('▶️ Принята: ' + req.accepted_at.strftime('%d.%m.%Y в %H:%M')) if req.accepted_at else ''}\n"
        f"{('✅ Закрыта: ' + req.closed_at.strftime('%d.%m.%Y в %H:%M')) if req.closed_at else ''}"
    )

    llm_flag = " ✨ ИИ" if getattr(req, "llm_meta", None) else ""
    if llm_flag:
        text = text.replace("🧾 <b>", "✨ ИИ-триаж применён\n🧾 <b>", 1)
    # Start from existing dispatcher_request_keyboard but add back button
    base_kb = dispatcher_request_keyboard(req.id, req.status)
    # base_kb has rows of 1 button each (or 2), we keep them
    rows = [list(row) for row in base_kb.inline_keyboard]
    # insert ✨ row after action rows, before back
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


async def _dispatcher_counts(session: AsyncSession) -> tuple[dict[str, int], dict[str, int]]:
    status_result = await session.execute(
        select(Request.status, func.count(Request.id)).group_by(Request.status)
    )
    category_result = await session.execute(
        select(Request.category, func.count(Request.id)).group_by(Request.category)
    )
    return dict(status_result.all()), dict(category_result.all())


@router.message(F.text.in_({"📊 Сводка", "📊 Жиынтық"}))
async def dispatcher_summary(message: Message, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not user or not _is_dispatcher(user):
        await message.answer("Только для диспетчеров.")
        return

    statuses, categories = await _dispatcher_counts(session)
    workers_result = await session.execute(
        select(func.count()).select_from(User).where(
            User.role == "worker", User.is_approved.is_(True), User.is_on_shift.is_(True)
        )
    )
    on_shift = workers_result.scalar() or 0
    active = statuses.get("new", 0) + statuses.get("accepted", 0)
    lines = [
        "📊 <b>Сводка по дому</b>",
        "",
        f"🚨 Активных: <b>{active}</b>",
        f"🆕 Новых: <b>{statuses.get('new', 0)}</b>",
        f"🔧 В работе: <b>{statuses.get('accepted', 0)}</b>",
        f"✅ Закрытых: <b>{statuses.get('closed', 0)}</b>",
        f"🟢 Исполнителей на смене: <b>{on_shift}</b>",
        "",
        "<b>По категориям</b>",
    ]
    for code, label in CATEGORY_LABELS.items():
        lines.append(f"{label}: {categories.get(code, 0)}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Открыть все заявки", callback_data="disp_filter:0:all:all")],
        [
            InlineKeyboardButton(text="🆕 Новые", callback_data="disp_filter:0:new:all"),
            InlineKeyboardButton(text="🔧 В работе", callback_data="disp_filter:0:accepted:all"),
        ],
    ])
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@router.message(F.text.in_({"📋 Все заявки", "📋 Барлық өтінімдер"}))
async def all_requests(message: Message, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not user or not _is_dispatcher(user):
        await message.answer("Только для диспетчеров.")
        return
    text, kb = await build_dispatcher_list(session, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("disp_list:"))
async def disp_list(callback: CallbackQuery, session: AsyncSession):
    # also check auth
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    page = int(callback.data.split(":")[1])
    text, kb = await build_dispatcher_list(session, page)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        # if not modified, just answer
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("disp_filter:"))
async def disp_filter(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
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
    text, kb = await build_dispatcher_list(session, page, status, category)
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
):
    req_id = callback_data.request_id
    page = callback_data.page
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_request_detail(session, req_id, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(DispatcherFilteredRequestCallback.filter())
async def filtered_req_view(
    callback: CallbackQuery,
    callback_data: DispatcherFilteredRequestCallback,
    session: AsyncSession,
):
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_request_detail(
        session, callback_data.request_id, callback_data.page
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
    await callback.answer()


@router.callback_query(DispatcherHistoryCallback.filter())
async def request_history(
    callback: CallbackQuery,
    callback_data: DispatcherHistoryCallback,
    session: AsyncSession,
):
    actor_result = await session.execute(
        select(User).where(User.telegram_id == callback.from_user.id)
    )
    if not _is_dispatcher(actor_result.scalar_one_or_none()):
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
        "claimed": "принята исполнителем",
        "assigned": "назначена",
        "reassigned": "переназначена",
        "closed": "закрыта",
        "deleted": "удалена",
    }
    lines = [f"🕓 <b>История заявки #{callback_data.request_id}</b>"]
    if not events:
        lines.append("История пока отсутствует.")
    for event in events:
        timestamp = event.created_at.strftime("%d.%m.%Y %H:%M") if event.created_at else "—"
        actor = "система"
        if event.actor:
            actor = event.actor.full_name or str(event.actor.telegram_id)
        detail = f" — {escape(event.details)}" if event.details else ""
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
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_dispatcher_list(session, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("assign:") | F.data.startswith("reassign:"))
async def start_assign(callback: CallbackQuery, session: AsyncSession):
    request_id = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not user or not _is_dispatcher(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
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

    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
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
            await bot.send_message(worker.telegram_id, f"📌 Вам назначена заявка #{req.id}\n{escape(req.description[:500])}")
        except Exception:
            pass
        rres = await session.execute(select(User).where(User.id == req.resident_id))
        resident = rres.scalar_one_or_none()
        if resident:
            try:
                await bot.send_message(resident.telegram_id, f"📌 Ваша заявка #{req.id} назначена исполнителю {escape(worker.full_name or str(worker.telegram_id))}")
            except Exception:
                pass


@router.callback_query(F.data.startswith("cancel_assign:"))
async def cancel_assign(callback: CallbackQuery):
    await callback.message.edit_text("Назначение отменено")
    await callback.answer()


PEND_PAGE_SIZE = 5

async def build_pending_list(session: AsyncSession, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total_res = await session.execute(select(func.count()).select_from(User).where(User.is_approved.is_(False)))
    total = total_res.scalar() or 0
    total_pages = (total + PEND_PAGE_SIZE - 1) // PEND_PAGE_SIZE if total else 1
    page = max(0, min(page, total_pages - 1))
    res = await session.execute(select(User).where(User.is_approved.is_(False)).order_by(User.created_at.desc()).limit(PEND_PAGE_SIZE).offset(page * PEND_PAGE_SIZE))
    pending = res.scalars().all()
    if not pending:
        return "✅ Нет ожидающих подтверждения.", InlineKeyboardMarkup(inline_keyboard=[])
    lines = [f"⏳ <b>На подтверждение</b> — стр {page+1}/{total_pages} (всего {total})\nНажмите 📄 чтобы открыть карточку\n"]
    for u in pending:
        role_label = {"resident": "Житель", "worker": "Исполнитель"}.get(u.role, u.role)
        lines.append(f"<b>#{u.id}</b> {role_label} • TG <code>{u.telegram_id}</code> • {u.created_at.strftime('%d.%m %H:%M') if u.created_at else ''}\n{u.full_name or '—'} кв.{u.apartment or '—'} {f'({u.worker_category})' if u.worker_category else ''}\n")
    text = "\n".join(lines)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for u in pending:
        row.append(InlineKeyboardButton(text=f"📄 #{u.id}", callback_data=f"pend_view:{u.id}:{page}"))
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
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

async def build_pending_detail(session: AsyncSession, user_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    res = await session.execute(select(User).where(User.id == user_id))
    u = res.scalar_one_or_none()
    if not u:
        return "Не найден.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"pend_list:{page}")]])
    role_label = {"resident": "Житель", "worker": "Исполнитель"}.get(u.role, u.role)
    text = (
        f"⏳ {role_label} на подтверждение\n"
        f"ID: {u.id} | TG: <code>{u.telegram_id}</code>\n"
        f"Имя: {u.full_name or '—'} | Кв: {u.apartment or '—'}\n"
        f"Категория: {u.worker_category or '—'}\n"
        f"Роль: {u.role} | Одобрен: {u.is_approved}\n"
        f"Создан: {u.created_at.strftime('%d.%m %H:%M') if u.created_at else '—'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{u.id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{u.id}")],
        [InlineKeyboardButton(text="◀️ К списку", callback_data=f"pend_list:{page}")],
    ])
    return text, kb

# --- Pending approvals (spec: resident needs dispatcher approval) ---

@router.message(F.text.in_({"⏳ На подтверждение", "⏳ Растауға"}))
async def pending_approvals(message: Message, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    viewer = result.scalar_one_or_none()
    if not viewer or not _is_dispatcher(viewer):
        await message.answer("Только для диспетчеров.")
        return
    text, kb = await build_pending_list(session, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("pend_list:"))
async def pend_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    viewer = result.scalar_one_or_none()
    if not viewer or not _is_dispatcher(viewer):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_pending_list(session, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("pend_view:"))
async def pend_view(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    uid = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    viewer = result.scalar_one_or_none()
    if not viewer or not _is_dispatcher(viewer):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_pending_detail(session, uid, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("approve:"))
async def approve_user(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    actor_result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    if not _is_dispatcher(actor_result.scalar_one_or_none()):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_db_id = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.id == user_db_id))
    u = result.scalar_one_or_none()
    if not u:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    if u.is_approved:
        await callback.answer("Уже одобрен", show_alert=True)
        return
    u.is_approved = True
    await session.commit()
    await callback.message.edit_text(callback.message.text + "\n\n✅ Одобрен")
    await callback.answer("Одобрен")
    try:
        await bot.send_message(u.telegram_id, "✅ Ваша регистрация подтверждена диспетчером! Теперь можете создать заявку: /start")
    except Exception:
        pass


@router.callback_query(F.data.startswith("reject:"))
async def reject_user(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    actor_result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    if not _is_dispatcher(actor_result.scalar_one_or_none()):
        await callback.answer("Нет прав", show_alert=True)
        return
    user_db_id = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.id == user_db_id))
    u = result.scalar_one_or_none()
    if not u:
        await callback.answer("Не найден", show_alert=True)
        return
    tid = u.telegram_id
    await session.delete(u)
    await session.commit()
    await callback.message.edit_text(callback.message.text + "\n\n❌ Отклонен")
    await callback.answer("Отклонен")
    try:
        await bot.send_message(tid, "❌ Ваша регистрация отклонена диспетчером. Обратитесь в диспетчерскую.")
    except Exception:
        pass


# Announcements

@router.message(F.text.in_({"📢 Создать объявление", "📢 Хабарландыру жасау"}))
async def create_ann_start(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
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

    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
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
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
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
    actor_result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    if not _is_dispatcher(actor_result.scalar_one_or_none()):
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
            f"✨ <b>ИИ-триаж #{req.id}</b>\nПриоритет: <b>{escape(tri.priority)}</b>\n"
            f"Кратко: {escape(tri.summary)}\nПодсказка: {escape(tri.hint)}",
            parse_mode="HTML"
        )
    except Exception:
        logger.exception("request_triage_failed request_id=%s", req.id)
        await callback.message.answer("⚠️ ИИ временно недоступен. Попробуйте позже.")


# Add worker

@router.message(F.text.in_({"➕ Добавить исполнителя", "➕ Орындаушы қосу"}))
async def add_worker_start(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
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
    actor_result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    if not _is_dispatcher(actor_result.scalar_one_or_none()):
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
