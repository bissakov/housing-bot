from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models import User, Request
from bot.states import RequestStates
from bot.keyboards import category_keyboard, category_keyboard_with_cancel, CATEGORY_LABELS, STATUS_LABELS, resident_menu, resident_request_keyboard, cancel_keyboard
from bot.services.requests import create_request, get_requests_for_resident
from bot.services.llm import get_llm
import json
import logging
from bot.services.notify import notify_workers, notify_dispatchers
from html import escape
from bot.auth import is_approved_resident
from bot.callbacks import ResidentRequestCallback
from bot.constants import URGENCY_LABELS

router = Router()
logger = logging.getLogger(__name__)

PAGE_SIZE = 5

@router.callback_query(F.data == "cancel_fsm")
async def resident_cancel_cb(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    from sqlalchemy import select as _sel
    res = await session.execute(_sel(User).where(User.telegram_id == callback.from_user.id))
    u = res.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    try:
        await callback.message.edit_text("❌ Отмена")
    except Exception:
        pass
    if kb:
        await callback.message.answer("Главное меню", reply_markup=kb)
    await callback.answer()

@router.message(F.text == "❌ Отмена")
async def resident_cancel_text(message: Message, state: FSMContext, session: AsyncSession):
    if await state.get_state() is None:
        return
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    from sqlalchemy import select as _sel
    res = await session.execute(_sel(User).where(User.telegram_id == message.from_user.id))
    u = res.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await message.answer("❌ Отменено.", reply_markup=kb)

def _is_resident(user: User) -> bool:
    from bot.config import ADMIN_IDS
    if user.telegram_id in ADMIN_IDS:
        return False
    return user.role == "resident"


async def build_resident_list(session: AsyncSession, resident_db_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total_res = await session.execute(select(func.count()).select_from(Request).where(Request.resident_id == resident_db_id))
    total = total_res.scalar() or 0
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    page = max(0, min(page, total_pages - 1))

    q = await session.execute(
        select(Request)
        .options(selectinload(Request.worker))
        .where(Request.resident_id == resident_db_id)
        .order_by(Request.created_at.desc())
        .limit(PAGE_SIZE)
        .offset(page * PAGE_SIZE)
    )
    reqs = q.scalars().all()

    if not reqs:
        return (
            "📭 <b>Заявок пока нет</b>\n\nСоздайте первую заявку — это займёт меньше минуты.",
            InlineKeyboardMarkup(inline_keyboard=[]),
        )

    lines = [f"📋 <b>Мои заявки</b> — {page+1}/{total_pages} • всего {total}\n"]
    for req in reqs:
        w_str = ""
        if req.worker:
            w_str = f" → {escape(req.worker.full_name or str(req.worker.telegram_id))}"
        desc = escape(req.description.strip().replace("\n", " "))
        if len(desc) > 60:
            desc = desc[:60] + "…"
        date = req.created_at.strftime("%d.%m %H:%M") if req.created_at else ""
        lines.append(f"<b>#{req.id}</b> {CATEGORY_LABELS.get(req.category, req.category)} {STATUS_LABELS.get(req.status, req.status)}{w_str} • {date}\n<i>{desc}</i>\n")

    text = "\n".join(lines)

    kb_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for req in reqs:
        btn = InlineKeyboardButton(text=f"📄 #{req.id}", callback_data=ResidentRequestCallback(request_id=req.id, page=page).pack())
        row.append(btn)
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    if total_pages > 1:
        pag_row: list[InlineKeyboardButton] = []
        if page > 0:
            pag_row.append(InlineKeyboardButton(text="◀️", callback_data=f"res_list:{page-1}"))
        pag_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pag_row.append(InlineKeyboardButton(text="▶️", callback_data=f"res_list:{page+1}"))
        kb_rows.append(pag_row)

    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)

async def build_resident_detail(session: AsyncSession, request_id: int, page: int, viewer: User) -> tuple[str, InlineKeyboardMarkup]:
    q = await session.execute(select(Request).where(Request.id == request_id, Request.resident_id == viewer.id))
    req = q.scalar_one_or_none()
    if not req:
        return "Заявка не найдена или не ваша.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"res_list:{page}")]])

    w_str = "—"
    if req.worker_id:
        wres = await session.execute(select(User).where(User.id == req.worker_id))
        w = wres.scalar_one_or_none()
        if w:
            w_str = w.full_name or str(w.telegram_id)

    text = (
        f"🧾 <b>Заявка #{req.id}</b>\n"
        f"{CATEGORY_LABELS.get(req.category, req.category)} • {STATUS_LABELS.get(req.status, req.status)}\n"
        f"{URGENCY_LABELS.get(req.urgency, req.urgency)} приоритет\n\n"
        f"🧰 <b>Исполнитель:</b> {escape(w_str)}\n\n"
        f"📝 <b>Описание</b>\n{escape(req.description)}\n\n"
        f"🕒 Создана: {req.created_at.strftime('%d.%m.%Y в %H:%M') if req.created_at else '—'}\n"
        f"{('▶️ Принята: ' + req.accepted_at.strftime('%d.%m.%Y в %H:%M')) if req.accepted_at else ''}\n"
        f"{('✅ Закрыта: ' + req.closed_at.strftime('%d.%m.%Y в %H:%M')) if req.closed_at else ''}"
    )
    # Spec: resident can NOT close; only delete own new. Hide Закрыть for resident.
    rows: list[list[InlineKeyboardButton]] = []
    if req.status == "new" and req.resident_id == viewer.id:
        rows.append([InlineKeyboardButton(text="🗑️ Удалить заявку", callback_data=f"delete_req:{req.id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data=f"res_list:{page}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "📝 Создать заявку")
async def start_request(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not is_approved_resident(user):
        await message.answer("Сначала завершите регистрацию через /start")
        return
    # LLM smart intake: if enabled, allow free-form without picking category first
    try:
        llm = get_llm()
        if llm.enabled:
            await message.answer(
                "Опишите проблему одним сообщением (например: \"течёт кран на кухне, лужа на полу\") —\n"
                "я сам определю категорию и улучшу описание.\n"
                "Или выберите категорию вручную:",
                reply_markup=category_keyboard_with_cancel("req_category"),
            )
            await state.set_state(RequestStates.waiting_description)
            await state.update_data(category=None, llm_intake=True)
            return
    except Exception:
        pass
    await message.answer(
        "📝 <b>Новая заявка</b>\n\nШаг 1 из 2 — выберите категорию:",
        parse_mode="HTML",
        reply_markup=category_keyboard_with_cancel("req_category"),
    )
    await state.set_state(RequestStates.waiting_category)


@router.callback_query(F.data.startswith("req_category:"))
async def choose_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":")[1]
    if category not in CATEGORY_LABELS:
        await callback.answer("Неизвестная категория")
        return
    await state.update_data(category=category, llm_intake=False)
    await callback.message.edit_text(
        f"📝 <b>Новая заявка</b>\n\n"
        f"Шаг 2 из 2 — опишите проблему\n"
        f"Категория: {CATEGORY_LABELS[category]}\n\n"
        "Укажите, что произошло и где именно. Например: «На кухне под мойкой течёт труба».",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(RequestStates.waiting_description)
    await callback.answer()


@router.message(RequestStates.waiting_description, F.text)
async def input_description(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    category = data.get("category")
    llm_intake = bool(data.get("llm_intake")) and category is None
    description_raw = message.text.strip()
    if len(description_raw) < 10:
        await message.answer(
            "Опишите проблему подробнее: добавьте место и детали — минимум 10 символов."
        )
        return

    # --- LLM auto-classify + enrich (OpenAI-compatible) ---
    description = description_raw
    urgency = None
    raw_description = None
    llm_meta = None
    chosen_category = category
    auto_conf = 0.0
    if not category or llm_intake:
        try:
            llm = get_llm()
            if llm.enabled:
                from bot.config import LLM_AUTO_CATEGORY_THRESHOLD
                res = await llm.classify_and_enrich(description_raw)
                # only enrich if we got something usable
                if res.enriched and len(res.enriched) >= 10:
                    description = res.enriched
                    urgency = res.urgency
                    raw_description = description_raw
                    llm_meta = json.dumps({"category": res.category, "confidence": res.confidence, "reason": res.reason, "urgency": res.urgency}, ensure_ascii=False)
                    auto_conf = res.confidence
                    # decide category
                    if res.confidence >= LLM_AUTO_CATEGORY_THRESHOLD:
                        chosen_category = res.category
                    else:
                        # low confidence -> ask confirm
                        await state.update_data(
                            pending_category=res.category,
                            pending_confidence=res.confidence,
                            pending_enriched=description,
                            pending_urgency=urgency,
                            pending_raw=description_raw,
                            pending_meta=llm_meta,
                        )
                        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text=f"✅ {CATEGORY_LABELS[res.category]} (уверенность {res.confidence:.0%})", callback_data="llm_confirm:yes"),
                             InlineKeyboardButton(text="Выбрать вручную", callback_data="llm_confirm:no")],
                            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
                        ])
                        await message.answer(
                            f"Я определил категорию как <b>{CATEGORY_LABELS[res.category]}</b> (уверенность {res.confidence:.0%}, приоритет {urgency}).\n"
                            f"Улучшенное описание: <i>{description[:400]}</i>\n\nПодтвердить?",
                            parse_mode="HTML", reply_markup=kb
                        )
                        return
        except Exception:
            logger.exception("request_classification_failed")
            # fallback to deterministic: need category
            if not chosen_category:
                await message.answer("Не удалось определить категорию автоматически. Выберите вручную:", reply_markup=category_keyboard_with_cancel("req_category"))
                await state.set_state(RequestStates.waiting_category)
                # stash raw to reuse after pick
                await state.update_data(pending_enriched=description_raw, pending_raw=description_raw)
                return
    if not chosen_category:
        await message.answer("Ошибка категории, начните заново: 📝 Создать заявку")
        await state.clear()
        return
    category = chosen_category
    # if llm had pending enrichment from prior freeform, reuse when user now picked manually
    if not llm_meta and data.get("pending_enriched") and description == description_raw:
        if len(data.get("pending_enriched","")) >= 10:
            description = data.get("pending_enriched")
            raw_description = data.get("pending_raw") or description_raw
            llm_meta = data.get("pending_meta")
            urgency = data.get("pending_urgency") or urgency

    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not is_approved_resident(user):
        await message.answer("Ошибка пользователя")
        await state.clear()
        return

    req = await create_request(session, resident_id=user.id, category=category, description=description, urgency=urgency, raw_description=raw_description, llm_meta=llm_meta)
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>Заявка #{req.id} создана</b>\n\n"
        f"{CATEGORY_LABELS[category]}{' • ✨ обработано ИИ' if llm_meta else ''}\n"
        f"{STATUS_LABELS['new']} • {URGENCY_LABELS.get(urgency or 'normal')} приоритет\n\n"
        "Мы уведомили подходящих исполнителей. Статус можно проверить в разделе «📋 Мои заявки».",
        parse_mode="HTML",
        reply_markup=resident_menu()
    )

    notify_text = (
        f"🆕 <b>Новая заявка #{req.id}</b>\n"
        f"Категория: {CATEGORY_LABELS[category]}\n"
        f"Адрес: кв. {escape(user.apartment or '?')} | {escape(user.full_name or '')}\n"
        f"Описание: {escape(description[:500])}\n\n"
        f"Нажмите «📋 Доступные заявки» чтобы принять."
    )
    report = await notify_workers(bot, session, category, notify_text)
    if report.delivered == 0:
        await notify_dispatchers(
            bot,
            session,
            f"⚠️ Для новой заявки #{req.id} нет доступных исполнителей на смене.",
        )


@router.callback_query(F.data.startswith("llm_confirm:"))
async def llm_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if choice == "yes":
        category = data.get("pending_category")
        description = data.get("pending_enriched") or data.get("pending_raw") or ""
        urgency = data.get("pending_urgency")
        raw_description = data.get("pending_raw")
        llm_meta = data.get("pending_meta")
        result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        user = result.scalar_one_or_none()
        if not is_approved_resident(user):
            await callback.answer("Ошибка", show_alert=True)
            return
        req = await create_request(session, resident_id=user.id, category=category, description=description, urgency=urgency, raw_description=raw_description, llm_meta=llm_meta)
        await session.commit()
        await state.clear()
        await callback.message.edit_text(
            f"✅ Заявка #{req.id} создана!\nКатегория: {CATEGORY_LABELS.get(category, category)} ✨ ИИ · {STATUS_LABELS['new']}"
        )
        await callback.message.answer("Исполнители на смене получили уведомление.", reply_markup=resident_menu())
        notify_text = (
            f"🆕 <b>Новая заявка #{req.id}</b>\n"
            f"Категория: {CATEGORY_LABELS[category]}\n"
            f"Адрес: кв. {escape(user.apartment or '?')} | {escape(user.full_name or '')}\n"
            f"Описание: {escape(description[:500])}\n\n"
            f"Нажмите «📋 Доступные заявки» чтобы принять."
        )
        report = await notify_workers(bot, session, category, notify_text)
        if report.delivered == 0:
            await notify_dispatchers(
                bot,
                session,
                f"⚠️ Для новой заявки #{req.id} нет доступных исполнителей на смене.",
            )
        await callback.answer()
    else:
        # manual pick
        await state.update_data(category=None)
        await callback.message.edit_text("Выберите категорию вручную:", reply_markup=category_keyboard_with_cancel("req_category"))
        await state.set_state(RequestStates.waiting_category)
        await callback.answer()


@router.message(F.text == "📋 Мои заявки")
async def my_requests(message: Message, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not is_approved_resident(user):
        await message.answer("Сначала /start")
        return
    text, kb = await build_resident_list(session, user.id, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("res_list:"))
async def res_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not is_approved_resident(user):
        await callback.answer("Ошибка", show_alert=True)
        return
    text, kb = await build_resident_list(session, user.id, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(ResidentRequestCallback.filter())
async def res_view(
    callback: CallbackQuery,
    callback_data: ResidentRequestCallback,
    session: AsyncSession,
):
    req_id = callback_data.request_id
    page = callback_data.page
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not is_approved_resident(user):
        await callback.answer("Ошибка", show_alert=True)
        return
    text, kb = await build_resident_detail(session, req_id, page, user)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()
