"""Administrator-only actions presented in the UI as chairman actions."""

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.auth import is_administrator
from bot.config import ADMIN_IDS
from bot.handlers.common import get_main_keyboard
from bot.i18n import role_label, text_variants
from bot.keyboards import reply_cancel_keyboard
from bot.models import Request, User
from bot.services.identity import get_actor
from bot.services.notify import notify_resident, notify_workers, send_to_user
from bot.services.requests import approve_request, reject_request
from bot.states import RequestApprovalStates


router = Router()
logger = logging.getLogger(__name__)


async def _chairman(session: AsyncSession, telegram_id: int) -> User | None:
    user = await get_actor(session, telegram_id)
    return user if is_administrator(user) else None


@router.message(F.text.in_(text_variants("participants")))
async def list_participants(message: Message, session: AsyncSession):
    actor = await _chairman(session, message.from_user.id)
    if not actor:
        await message.answer("Только для председателя.")
        return
    result = await session.execute(
        select(User)
        .where(User.is_approved.is_(True), User.id != actor.id)
        .order_by(User.role, User.full_name, User.id)
        .limit(50)
    )
    users = list(result.scalars())
    if not users:
        await message.answer("Других активных участников нет.")
        return
    rows = [[InlineKeyboardButton(
        text=(
            f"❌ {user.full_name or user.telegram_id} · "
            f"{role_label(user.role, actor.language)}"
        ),
        callback_data=f"participant_revoke:{user.id}",
    )] for user in users]
    await message.answer(
        "👥 <b>Активные участники</b>\n\n"
        "Нажатие откроет подтверждение отзыва доступа.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("participant_revoke:"))
async def confirm_participant_revoke(
    callback: CallbackQuery, session: AsyncSession
):
    actor = await _chairman(session, callback.from_user.id)
    user_id = int(callback.data.split(":", 1)[1])
    target = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if (
        not actor or not target or target.id == actor.id
        or target.telegram_id in ADMIN_IDS
    ):
        await callback.answer("Доступ этого участника нельзя отозвать", show_alert=True)
        return
    await callback.message.answer(
        f"Отозвать доступ у {escape(target.full_name or str(target.telegram_id))}?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Да, отозвать",
                callback_data=f"participant_confirm_revoke:{target.id}",
            ),
            InlineKeyboardButton(text="❌ Нет", callback_data="cancel_fsm"),
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("participant_confirm_revoke:"))
async def revoke_participant(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
):
    actor = await _chairman(session, callback.from_user.id)
    user_id = int(callback.data.split(":", 1)[1])
    target = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if (
        not actor or not target or target.id == actor.id
        or target.telegram_id in ADMIN_IDS
    ):
        await callback.answer("Доступ этого участника нельзя отозвать", show_alert=True)
        return
    target.is_approved = False
    target.is_on_shift = False
    target.approved_by_owner_id = None
    await session.commit()
    try:
        await send_to_user(
            bot, session, target, "Председатель отозвал ваш доступ к боту."
        )
    except Exception:
        logger.info("revoked_participant_unreachable user_id=%s", target.id)
    await callback.message.edit_text("✅ Доступ участника отозван.")
    await callback.answer()


@router.callback_query(F.data.startswith("request_approve:"))
async def approve_kazakhdomofon_request(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
):
    actor = await _chairman(session, callback.from_user.id)
    if not actor:
        await callback.answer("Только для председателя", show_alert=True)
        return
    request_id = int(callback.data.split(":", 1)[1])
    success, message = await approve_request(session, request_id, actor)
    if not success:
        await callback.answer(message, show_alert=True)
        return
    await session.commit()
    request = (
        await session.execute(select(Request).where(Request.id == request_id))
    ).scalar_one()
    resident = (
        await session.execute(select(User).where(User.id == request.resident_id))
    ).scalar_one_or_none()
    await notify_workers(
        bot,
        session,
        request.category,
        f"📹 Заявка Казахдомофон #{request.id} согласована и доступна для исполнения.",
    )
    if resident:
        await notify_resident(
            bot,
            session,
            resident,
            f"✅ Ваша заявка #{request.id} согласована председателем и "
            "направлена Казахдомофон.",
        )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Заявка согласована")


@router.callback_query(F.data.startswith("request_reject:"))
async def start_reject_kazakhdomofon_request(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
):
    actor = await _chairman(session, callback.from_user.id)
    if not actor:
        await callback.answer("Только для председателя", show_alert=True)
        return
    request_id = int(callback.data.split(":", 1)[1])
    request = (
        await session.execute(select(Request).where(Request.id == request_id))
    ).scalar_one_or_none()
    if not request or request.approval_status != "pending":
        await callback.answer("Заявка уже рассмотрена", show_alert=True)
        return
    await state.set_state(RequestApprovalStates.waiting_rejection_comment)
    await state.set_data({"request_id": request_id})
    await callback.message.answer(
        "Укажите причину отклонения. Комментарий обязателен.",
        reply_markup=reply_cancel_keyboard(actor.language),
    )
    await callback.answer()


@router.message(RequestApprovalStates.waiting_rejection_comment)
async def finish_reject_kazakhdomofon_request(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
):
    comment = (message.text or "").strip()
    if not comment:
        await message.answer("Комментарий обязателен.")
        return
    actor = await _chairman(session, message.from_user.id)
    data = await state.get_data()
    request_id = data.get("request_id")
    if not actor or not request_id:
        await state.clear()
        await message.answer("Форма устарела.")
        return
    request = (
        await session.execute(select(Request).where(Request.id == request_id))
    ).scalar_one_or_none()
    resident = None
    if request:
        resident = (
            await session.execute(select(User).where(User.id == request.resident_id))
        ).scalar_one_or_none()
    success, result_message = await reject_request(
        session, request_id, actor, comment
    )
    if not success:
        await message.answer(result_message)
        return
    await session.commit()
    await state.clear()
    if resident:
        await notify_resident(
            bot,
            session,
            resident,
            f"❌ Ваша заявка #{request_id} отклонена председателем.\n"
            f"Комментарий: {comment}",
        )
    await message.answer(
        f"Заявка #{request_id} отклонена.",
        reply_markup=get_main_keyboard(actor),
    )
