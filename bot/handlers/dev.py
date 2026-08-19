"""
DEV-only: /dev role switch available to everyone when DEV_MODE=true.
Guarded by config.DEV_MODE — never loaded in prod.
"""
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import User
from bot.config import DEV_MODE
from bot.handlers.common import get_main_keyboard

router = Router()

ROLE_OPTIONS = [
    ("resident", "🏠 Житель"),
    ("worker:electrician", "🔌 Электрик"),
    ("worker:plumber", "🚿 Сантехник"),
    ("worker:security", "🛡️ Охрана"),
    ("dispatcher", "🎛️ Диспетчер"),
]

def dev_keyboard(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for val, label in ROLE_OPTIONS:
        prefix = "✅ " if val == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"dev_switch:{val}")
    b.adjust(2, 2, 1)
    return b.as_markup()

def current_role_str(user: User) -> str:
    if user.role == "worker":
        return f"worker:{user.worker_category or 'plumber'}"
    return user.role

@router.message(F.text == "/dev")
async def dev_entry(message: Message, session: AsyncSession):
    if not DEV_MODE:
        await message.answer("⛔ DEV mode disabled. Set DEV_MODE=true in .env to enable /dev.")
        return
    res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала /start")
        return
    cur = current_role_str(user)
    await message.answer(
        f"🔧 <b>DEV режим</b> — переключение роли\n"
        f"Текущая: <b>{cur}</b> | approved={user.is_approved} | на смене={user.is_on_shift}\n"
        f"Выбери новую роль (сработает сразу, без перезапуска):",
        parse_mode="HTML",
        reply_markup=dev_keyboard(cur),
    )

@router.callback_query(F.data.startswith("dev_switch:"))
async def dev_switch(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not DEV_MODE:
        await callback.answer("DEV disabled", show_alert=True)
        return
    choice = callback.data.split("dev_switch:")[1]  # resident | worker:xxx | dispatcher
    res = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = res.scalar_one_or_none()
    if not user:
        await callback.answer("Сначала /start", show_alert=True)
        return

    # apply switch
    if choice.startswith("worker:"):
        cat = choice.split(":")[1]
        user.role = "worker"
        user.worker_category = cat
        user.is_approved = True
        # FIX: /dev used to flip only role/category — full_name stayed "Admin" from the
        # initial ADMIN_IDS /dispatcher row, so claim/close notifications kept saying "Admin".
        # If stale, replace with real Telegram name.
        if not user.full_name or user.full_name.strip().lower() == "admin":
            tg_name = (callback.from_user.full_name or callback.from_user.first_name or "").strip()
            if tg_name and tg_name.lower() != "admin":
                user.full_name = tg_name
        # keep is_on_shift as is; if switching from non-worker default to on so you can test immediately
        if not hasattr(user, "_was_worker"):
            pass
    elif choice == "resident":
        user.role = "resident"
        user.worker_category = None
        user.is_approved = True
        user.is_on_shift = False
        # ensure resident has name/apt so FSM doesn't block; fill dummy if missing
        if not user.full_name or user.full_name.strip().lower() == "admin":
            tg_name = (callback.from_user.full_name or callback.from_user.first_name or "").strip()
            user.full_name = tg_name if tg_name and tg_name.lower() != "admin" else "DEV Житель"
        if not user.apartment:
            user.apartment = "1"
    elif choice == "dispatcher":
        user.role = "dispatcher"
        user.worker_category = None
        user.is_approved = True
        user.is_on_shift = False

    await session.commit()

    kb = get_main_keyboard(user)
    role_label = {"resident": "Житель", "worker": "Исполнитель", "dispatcher": "Диспетчер"}.get(user.role, user.role)
    extra = f" ({user.worker_category})" if user.role == "worker" else ""
    await callback.message.edit_text(f"✅ Роль → <b>{role_label}{extra}</b>\nМеню обновлено.", parse_mode="HTML")
    await callback.message.answer(f"Главное меню | Роль: {role_label}{extra}", reply_markup=kb)
    await callback.answer(f"Switched to {choice}")
