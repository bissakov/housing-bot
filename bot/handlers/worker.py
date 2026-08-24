import json

from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from html import escape

from bot.models import User, Request
from bot.keyboards import (
    CATEGORY_LABELS,
    STATUS_LABELS,
    worker_menu,
    request_claim_keyboard,
    reply_cancel_keyboard,
)
from bot.services.requests import claim_request, close_request, get_requests_for_worker
from bot.services.notify import notify_resident, notify_dispatchers
from bot.services.identity import get_actor
from bot.auth import is_approved_worker, can_view_available_request, can_view_assigned_request
from bot.callbacks import WorkerAvailableCallback, WorkerAssignedCallback
from bot.constants import URGENCY_LABELS
from bot.services.llm import get_llm
from bot.states import WorkerCompletionStates
from bot.timezone import format_local, utc_now
from bot.services.schedules import get_schedule_status
from bot.services.request_routing import worker_ready_expression
from bot.i18n import t, text_variants

router = Router()

PAGE_SIZE = 5


def _priority_order():
    """High-priority open work stays first; closing it promotes the next item."""
    return (
        case((Request.urgency == "high", 0), (Request.urgency == "normal", 1),
             (Request.urgency == "low", 2), else_=3),
        Request.is_escalated.desc(),
        Request.created_at.asc(),
    )

async def build_worker_available(session: AsyncSession, user: User, page: int) -> tuple[str, InlineKeyboardMarkup]:
    from sqlalchemy import func
    # total new of this category
    ready = worker_ready_expression(utc_now())
    total_res = await session.execute(select(func.count()).select_from(Request).where(
        Request.category == user.worker_category, Request.status == "new", *ready
    ))
    total = total_res.scalar() or 0
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    page = max(0, min(page, total_pages - 1))
    q = await session.execute(
        select(Request)
        .options(selectinload(Request.resident))
        .where(Request.category == user.worker_category, Request.status == "new", *ready)
        .order_by(*_priority_order()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
    )
    reqs = q.scalars().all()
    if not reqs:
        return "🎉 <b>Новых заявок нет</b>\n\nВ вашей категории сейчас всё спокойно.", InlineKeyboardMarkup(inline_keyboard=[])
    category_label = CATEGORY_LABELS.get(user.worker_category, user.worker_category)
    lines = [f"📋 <b>Доступные заявки</b> — {page+1}/{total_pages} • всего {total}\n{category_label}\n"]
    for req in reqs:
        resident = req.resident
        addr = f"кв. {resident.apartment}" if resident and resident.apartment else ""
        desc = escape(req.description.strip().replace("\n", " "))
        if len(desc) > 55:
            desc = desc[:55] + "…"
        date = format_local(req.created_at, "%d.%m %H:%M", "")
        priority = URGENCY_LABELS.get(req.urgency, "Обычный")
        lines.append(f"<b>#{req.id}</b> • {priority} • {escape(addr)} {escape(resident.full_name if resident else '')} • {date}\n<i>{desc}</i>\n")
    text = "\n".join(lines)
    kb_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for req in reqs:
        row.append(InlineKeyboardButton(text=f"📄 #{req.id}", callback_data=WorkerAvailableCallback(request_id=req.id, page=page).pack()))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    if total_pages > 1:
        pag: list[InlineKeyboardButton] = []
        if page > 0:
            pag.append(InlineKeyboardButton(text="◀️", callback_data=f"w_av_list:{page-1}"))
        pag.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pag.append(InlineKeyboardButton(text="▶️", callback_data=f"w_av_list:{page+1}"))
        kb_rows.append(pag)
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)

async def build_worker_mine(session: AsyncSession, user: User, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total_res = await session.execute(select(func.count()).select_from(Request).where(Request.worker_id == user.id, Request.status == "accepted"))
    total = total_res.scalar() or 0
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    page = max(0, min(page, total_pages - 1))
    q = await session.execute(
        select(Request)
        .options(selectinload(Request.resident))
        .where(Request.worker_id == user.id, Request.status == "accepted")
        .order_by(*_priority_order()).limit(PAGE_SIZE).offset(page * PAGE_SIZE)
    )
    reqs = q.scalars().all()
    if not reqs:
        return "✅ <b>У вас нет заявок в работе</b>\n\nМожно принять новую в разделе «📋 Доступные заявки».", InlineKeyboardMarkup(inline_keyboard=[])
    lines = [f"🔧 <b>Мои заявки</b> — {page+1}/{total_pages} • всего {total}\n"]
    for req in reqs:
        resident = req.resident
        addr = f"кв. {resident.apartment}" if resident and resident.apartment else ""
        desc = escape(req.description.strip().replace("\n", " "))
        if len(desc) > 55:
            desc = desc[:55] + "…"
        lines.append(f"<b>#{req.id}</b> {CATEGORY_LABELS.get(req.category, req.category)} {addr} • {desc}\n")
    text = "\n".join(lines)
    kb_rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for req in reqs:
        row.append(InlineKeyboardButton(text=f"📄 #{req.id}", callback_data=WorkerAssignedCallback(request_id=req.id, page=page).pack()))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    if total_pages > 1:
        pag: list[InlineKeyboardButton] = []
        if page > 0:
            pag.append(InlineKeyboardButton(text="◀️", callback_data=f"w_my_list:{page-1}"))
        pag.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pag.append(InlineKeyboardButton(text="▶️", callback_data=f"w_my_list:{page+1}"))
        kb_rows.append(pag)
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@router.message(F.text.in_(text_variants("shift_on") | text_variants("shift_off")))
async def toggle_shift(message: Message, session: AsyncSession):
    user = await get_actor(session, message.from_user.id)
    if not is_approved_worker(user):
        await message.answer("Только для исполнителей.")
        return
    user.is_on_shift = not user.is_on_shift
    await session.commit()
    status = "🟢 Вы на смене. Новые заявки будут доступны в меню." if user.is_on_shift else "⚪ Смена завершена. Уведомления о новых заявках приостановлены."
    await message.answer(status, reply_markup=worker_menu(user.is_on_shift, user.language))


@router.message(F.text.in_(text_variants("available_requests")))
async def available_requests(message: Message, session: AsyncSession):
    user = await get_actor(session, message.from_user.id)
    if not is_approved_worker(user):
        await message.answer("Доступно только исполнителям.")
        return
    if not user.is_on_shift:
        await message.answer(t("start_shift_first", user.language))
        return
    if not (await get_schedule_status(session, user)).planned:
        await message.answer(t("not_scheduled_now", user.language))
        return
    text, kb = await build_worker_available(session, user, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(F.text.in_(text_variants("worker_my_requests")))
async def my_worker_requests(message: Message, session: AsyncSession):
    user = await get_actor(session, message.from_user.id)
    if not is_approved_worker(user):
        await message.answer("Только для исполнителей.")
        return
    text, kb = await build_worker_mine(session, user, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("w_av_list:"))
async def w_av_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    user = await get_actor(session, callback.from_user.id)
    if not is_approved_worker(user) or not user.is_on_shift:
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_worker_available(session, user, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("w_my_list:"))
async def w_my_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    user = await get_actor(session, callback.from_user.id)
    if not is_approved_worker(user):
        await callback.answer("Нет прав", show_alert=True)
        return
    text, kb = await build_worker_mine(session, user, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(WorkerAvailableCallback.filter())
async def w_av_view(
    callback: CallbackQuery,
    callback_data: WorkerAvailableCallback,
    session: AsyncSession,
    bot: Bot,
):
    req_id = callback_data.request_id
    page = callback_data.page
    q = await session.execute(
        select(Request)
        .options(selectinload(Request.attachments))
        .where(Request.id == req_id)
    )
    req = q.scalar_one_or_none()
    if not req:
        await callback.answer("Не найдена", show_alert=True)
        return
    if not can_view_available_request(
        await get_actor(session, callback.from_user.id), req
    ):
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    rres = await session.execute(select(User).where(User.id == req.resident_id))
    resident = rres.scalar_one_or_none()
    addr = f"кв. {resident.apartment}" if resident and resident.apartment else ""
    text = (
        f"🧾 <b>Заявка #{req.id}</b>\n"
        f"{CATEGORY_LABELS.get(req.category, req.category)} • {URGENCY_LABELS.get(req.urgency, req.urgency)} приоритет\n\n"
        f"📍 <b>Адрес:</b> {escape(addr)}\n"
        f"👤 <b>Житель:</b> {escape(resident.full_name if resident else '—')}\n\n"
        f"📝 <b>Описание</b>\n{escape(req.description)}\n\n"
        f"{'📍 <b>Место:</b> внутри квартиры' + chr(10) if req.service_area == 'apartment' else ''}"
        f"{'📍 <b>Место:</b> МОП / общее имущество' + chr(10) if req.service_area == 'common' else ''}"
        f"{'📎 <b>Вложений:</b> ' + str(len(req.attachments)) + chr(10) if req.attachments else ''}"
        f"🕒 Создана: {format_local(req.created_at, '%d.%m.%Y в %H:%M')}"
    )
    # keep pagination back + claim
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"claim:{req.id}")],
        [InlineKeyboardButton(text="◀️ К списку", callback_data=f"w_av_list:{page}")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    for attachment in req.attachments:
        try:
            if attachment.media_type == "photo":
                await bot.send_photo(callback.from_user.id, attachment.file_id)
            elif attachment.media_type == "video":
                await bot.send_video(callback.from_user.id, attachment.file_id)
            else:
                await bot.send_document(callback.from_user.id, attachment.file_id)
        except Exception:
            pass
    await callback.answer()


@router.callback_query(WorkerAssignedCallback.filter())
async def w_my_view(
    callback: CallbackQuery,
    callback_data: WorkerAssignedCallback,
    session: AsyncSession,
):
    req_id = callback_data.request_id
    page = callback_data.page
    q = await session.execute(select(Request).where(Request.id == req_id))
    req = q.scalar_one_or_none()
    if not req:
        await callback.answer("Не найдена", show_alert=True)
        return
    if not can_view_assigned_request(
        await get_actor(session, callback.from_user.id), req
    ):
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    rres = await session.execute(select(User).where(User.id == req.resident_id))
    resident = rres.scalar_one_or_none()
    addr = f"кв. {resident.apartment}" if resident and resident.apartment else ""
    text = (
        f"🧾 <b>Заявка #{req.id}</b>\n"
        f"{STATUS_LABELS['accepted']} • {CATEGORY_LABELS.get(req.category, req.category)}\n"
        f"{URGENCY_LABELS.get(req.urgency, req.urgency)} приоритет\n\n"
        f"📍 <b>Адрес:</b> {escape(addr)}\n"
        f"👤 <b>Житель:</b> {escape(resident.full_name if resident else '—')}\n\n"
        f"📝 <b>Описание</b>\n{escape(req.description)}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"complete:{req.id}:done"),
            InlineKeyboardButton(text="❌ Не выполнено", callback_data=f"complete:{req.id}:not_done"),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data=f"w_my_list:{page}")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("noop"))
async def noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("claim:"))
async def handle_claim(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    request_id = int(callback.data.split(":")[1])
    worker = await get_actor(session, callback.from_user.id)
    if not is_approved_worker(worker):
        await callback.answer("Только для исполнителей", show_alert=True)
        return
    success, msg = await claim_request(session, request_id, worker)
    if not success:
        await callback.answer(msg, show_alert=True)
        return
    await session.commit()
    await callback.message.edit_text(callback.message.text + f"\n\n✅ Принята вами")
    await callback.answer("Заявка принята!")

    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if req:
        rres = await session.execute(select(User).where(User.id == req.resident_id))
        resident = rres.scalar_one_or_none()
        if resident:
            await notify_resident(
                bot,
                session,
                resident,
                "",
                language=resident.language,
                message_key="request_accepted_notification",
                message_values={
                    "id": req.id,
                    "worker": worker.full_name or worker.telegram_id,
                },
            )
        await notify_dispatchers(bot, session, f"✅ Заявка #{req.id} принята: {worker.full_name or worker.telegram_id} ({CATEGORY_LABELS.get(worker.worker_category,'')})")


@router.callback_query(F.data.startswith("complete:"))
async def choose_completion_result(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
):
    _, request_id_raw, completion_result = callback.data.split(":", 2)
    request_id = int(request_id_raw)
    actor = await get_actor(session, callback.from_user.id)
    if not is_approved_worker(actor):
        await callback.answer("Только для исполнителей", show_alert=True)
        return
    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if not req or req.status != "accepted" or not can_view_assigned_request(actor, req):
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    await state.set_state(WorkerCompletionStates.waiting_comment)
    await state.set_data({"request_id": request_id, "completion_result": completion_result})
    prompt = (
        "Кратко опишите, что было сделано. Комментарий обязателен."
        if completion_result == "done"
        else "Укажите причину, по которой заявку не удалось выполнить. Комментарий обязателен."
    )
    await callback.message.answer(prompt, reply_markup=reply_cancel_keyboard(actor.language))
    await callback.answer()


@router.message(WorkerCompletionStates.waiting_comment)
async def handle_completion_comment(
    message: Message, session: AsyncSession, state: FSMContext, bot: Bot
):
    raw_comment = (message.text or "").strip()
    if not raw_comment:
        await message.answer("Пожалуйста, отправьте текстовый комментарий.")
        return
    data = await state.get_data()
    request_id = data.get("request_id")
    completion_result = data.get("completion_result")
    actor = await get_actor(session, message.from_user.id)
    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if (
        not is_approved_worker(actor)
        or not req
        or req.status != "accepted"
        or not can_view_assigned_request(actor, req)
    ):
        await state.clear()
        await message.answer("Заявка больше недоступна.")
        return

    final_comment = raw_comment
    llm_meta = None
    llm = get_llm()
    if not llm.enabled:
        await message.answer(
            "Сервис ИИ временно недоступен, поэтому комментарий пока нельзя "
            "проверить. Попробуйте отправить его немного позже."
        )
        return
    try:
        review = await llm.improve_completion_comment(
            raw_comment, completion_result, req.description
        )
    except Exception:
        await message.answer(
            "Не удалось проверить комментарий с помощью ИИ. "
            "Ваш текст сохранён в форме — попробуйте отправить его ещё раз позже."
        )
        return
    if not review.accepted:
        suggestion = review.suggestion or (
            "Опишите выполненную работу." if completion_result == "done"
            else "Укажите конкретную причину невыполнения."
        )
        await message.answer(f"💡 {escape(suggestion)}", parse_mode="HTML")
        return
    final_comment = review.improved
    llm_meta = json.dumps(
        {"accepted": review.accepted, "suggestion": review.suggestion},
        ensure_ascii=False,
    )

    success, msg = await close_request(
        session,
        request_id,
        actor,
        completion_result=completion_result,
        completion_comment=final_comment,
        completion_raw_comment=raw_comment,
        completion_llm_meta=llm_meta,
    )
    if not success:
        await message.answer(msg)
        return
    await session.commit()
    await state.clear()
    result_label = "выполнена" if completion_result == "done" else "не выполнена"
    await message.answer(
        f"Заявка #{request_id} отмечена как «{result_label}».\n\n"
        f"Комментарий: {escape(final_comment)}",
        parse_mode="HTML",
        reply_markup=worker_menu(actor.is_on_shift, actor.language),
    )

    q = await session.execute(select(Request).where(Request.id == request_id))
    req = q.scalar_one_or_none()
    if req:
        rres = await session.execute(select(User).where(User.id == req.resident_id))
        resident = rres.scalar_one_or_none()
        if resident:
            icon = "✅" if completion_result == "done" else "❌"
            await notify_resident(
                bot,
                session,
                resident,
                f"{icon} Ваша заявка #{req.id} {result_label}.\n"
                f"Комментарий исполнителя: {final_comment}",
            )
        await notify_dispatchers(
            bot,
            session,
            f"Заявка #{req.id} {result_label} исполнителем "
            f"{actor.full_name or actor.telegram_id}.\nКомментарий: {final_comment}",
        )
