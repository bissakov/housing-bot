from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from html import escape
import logging

from bot.models import User
from bot.config import ADMIN_IDS
from bot.states import RegistrationStates
from bot.keyboards import resident_menu, worker_menu, dispatcher_menu, confirm_delete_keyboard, approval_keyboard, cancel_keyboard, reply_cancel_keyboard

router = Router()
logger = logging.getLogger(__name__)

PAGE_SIZE = 5

def get_main_keyboard(user: User):
    from bot.config import is_admin as _is_admin
    if _is_admin(user.telegram_id) and user.role == "dispatcher":
        return dispatcher_menu()
    if user.telegram_id in ADMIN_IDS and not __import__("bot.config", fromlist=["DEV_MODE"]).DEV_MODE:
        return dispatcher_menu()
    if user.role == "dispatcher":
        return dispatcher_menu()
    if user.role == "worker":
        return worker_menu(user.is_on_shift)
    return resident_menu()


def _is_dispatcher(user: User) -> bool:
    from bot.config import is_admin as _is_admin  # is_admin-gated
    return user.role == "dispatcher" or (not __import__("bot.config", fromlist=["DEV_MODE"]).DEV_MODE and user.telegram_id in ADMIN_IDS) or _is_admin(user.telegram_id) and user.role == "dispatcher"


@router.callback_query(F.data == "cancel_fsm")
async def cancel_fsm_cb(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    u = result.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await callback.message.edit_text("❌ Отмена")
    if kb:
        await callback.message.answer("Главное меню", reply_markup=kb)
    await callback.answer()

@router.message(F.text == "❌ Отмена")
async def cancel_fsm_text(message: Message, state: FSMContext, session: AsyncSession):
    cur = await state.get_state()
    if cur is None:
        return
    await state.clear()
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    u = result.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await message.answer("❌ Отменено.", reply_markup=kb)

async def ensure_user(message: Message, session: AsyncSession) -> User:  # type: ignore[no-redef]
    tid = message.from_user.id
    result = await session.execute(select(User).where(User.telegram_id == tid))
    user = result.scalar_one_or_none()
    if user:
        return user
    is_admin = tid in ADMIN_IDS
    role = "dispatcher" if is_admin else "resident"
    user = User(telegram_id=tid, role=role, is_approved=is_admin)
    session.add(user)
    await session.flush()
    return user


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    user = await ensure_user(message, session)
    await session.commit()

    if user.telegram_id in ADMIN_IDS and user.role != "dispatcher":
        user.role = "dispatcher"
        user.is_approved = True
        await session.commit()

    # Fresh user: offer resident vs worker self-registration before any text prompts
    if not user.is_approved and not user.full_name and not user.apartment and user.worker_category is None:
        from bot.keyboards import registration_role_keyboard
        await state.set_state(RegistrationStates.waiting_role)
        await message.answer(
            "👋 <b>Добро пожаловать в Домовой</b>\n\n"
            "Кем вы хотите зарегистрироваться?",
            parse_mode="HTML",
            reply_markup=registration_role_keyboard(),
        )
        return
    if not user.is_approved and user.role == "resident" and not user.full_name:
        await message.answer(
            "👋 <b>Добро пожаловать в Домовой</b>\n\n"
            "Здесь можно сообщать о проблемах в доме и следить за их решением.\n\n"
            "Шаг 1 из 2 — введите ваше ФИО:",
            parse_mode="HTML",
            reply_markup=reply_cancel_keyboard()
        )
        await state.set_state(RegistrationStates.waiting_name)
        return

    if not user.is_approved:
        if user.role == "worker":
            await message.answer("Ваша заявка на роль исполнителя ожидает подтверждения диспетчера.")
        else:
            await message.answer("Ваша регистрация ожидает подтверждения диспетчера. Пожалуйста, ожидайте.")
        return

    kb = get_main_keyboard(user)
    role_label = {"resident": "Житель", "worker": "Исполнитель", "dispatcher": "Диспетчер"}.get(user.role, user.role)
    pending_note = ""
    if _is_dispatcher(user):
        pend = await session.execute(select(User).where(User.is_approved.is_(False)))
        cnt = len(pend.scalars().all())
        if cnt:
            pending_note = f"\n⏳ Ожидают подтверждения: {cnt} — нажмите «⏳ На подтверждение»"
    name = escape(user.full_name) if user.full_name else role_label
    await message.answer(
        f"👋 <b>{name}</b>\n"
        f"Роль: {role_label}{pending_note}\n\n"
        "Выберите действие в меню ниже.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("reg_role:"))
async def reg_role_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if await state.get_state() != RegistrationStates.waiting_role:
        await callback.answer()
        return
    choice = callback.data.split(":", 1)[1]
    if choice == "worker":
        from bot.keyboards import registration_worker_category_keyboard
        result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        u = result.scalar_one_or_none()
        if u:
            u.role = "worker"
            await session.commit()
        await state.set_state(RegistrationStates.waiting_worker_category)
        await callback.message.edit_text("🔧 Выберите вашу дисциплину:", reply_markup=registration_worker_category_keyboard())
        await callback.answer()
        return
    # resident
    await callback.message.edit_text("Шаг 1 из 2 — введите ваше ФИО:")
    # keep reply keyboard separately
    await callback.message.answer("Введите ФИО:", reply_markup=__import__("bot.keyboards", fromlist=["reply_cancel_keyboard"]).reply_cancel_keyboard())
    await state.set_state(RegistrationStates.waiting_name)
    await callback.answer()


@router.callback_query(F.data.startswith("reg_worker_category:"))
async def reg_worker_category_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    if await state.get_state() != RegistrationStates.waiting_worker_category:
        await callback.answer()
        return
    cat = callback.data.split(":", 1)[1]
    from bot.constants import CATEGORY_CHOICES, CATEGORY_LABELS
    if cat not in CATEGORY_CHOICES:
        await callback.answer("Неизвестная категория", show_alert=True)
        return
    tid = callback.from_user.id
    result = await session.execute(select(User).where(User.telegram_id == tid))
    user = result.scalar_one_or_none()
    if not user:
        await callback.answer("Сначала /start", show_alert=True)
        await state.clear()
        return
    user.worker_category = cat
    user.role = "worker"
    user.is_approved = False
    if not user.full_name:
        user.full_name = (callback.from_user.full_name or f"ID {tid}").strip()
    await session.commit()
    await state.clear()
    label = CATEGORY_LABELS.get(cat, cat)
    await callback.message.edit_text(f"Спасибо! Дисциплина: <b>{label}</b>\n⏳ Ожидайте подтверждения диспетчера — вам придёт уведомление.", parse_mode="HTML")
    await callback.answer()
    # notify dispatchers reusing approval_keyboard
    text = f"🆕 <b>Новый исполнитель на подтверждение</b>\n{user.full_name}, категория: {label}, TG ID: <code>{tid}</code>\nНажмите ниже чтобы подтвердить:"
    res = await session.execute(select(User).where(User.role == "dispatcher"))
    for d in res.scalars().all():
        try:
            await bot.send_message(d.telegram_id, text, parse_mode="HTML", reply_markup=approval_keyboard(user.id))
        except Exception:
            import logging as _lg
            _lg.getLogger(__name__).exception("worker_notify_failed %s", d.telegram_id)
    if ADMIN_IDS:
        extra = await session.execute(select(User).where(User.telegram_id.in_(ADMIN_IDS)))
        for u in extra.scalars().all():
            if u.role != "dispatcher":
                try:
                    await bot.send_message(u.telegram_id, text, parse_mode="HTML", reply_markup=approval_keyboard(user.id))
                except Exception:
                    pass


@router.message(RegistrationStates.waiting_name, F.text)
async def reg_name(message: Message, state: FSMContext, session: AsyncSession):
    name = message.text.strip()
    if len(name) < 3:
        await message.answer("Введите корректное ФИО (минимум 3 символа):")
        return
    await state.update_data(full_name=name)
    await message.answer(
        "Шаг 2 из 2 — введите номер квартиры:", reply_markup=reply_cancel_keyboard()
    )
    await state.set_state(RegistrationStates.waiting_apartment)


@router.message(RegistrationStates.waiting_apartment, F.text)
async def reg_apartment(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    apartment = message.text.strip()
    data = await state.get_data()
    full_name = data.get("full_name")

    tid = message.from_user.id
    result = await session.execute(select(User).where(User.telegram_id == tid))
    user = result.scalar_one_or_none()
    if not user:
        await message.answer("Ошибка, попробуйте /start заново")
        await state.clear()
        return
    user.full_name = full_name
    user.apartment = apartment
    user.is_approved = False
    await session.commit()
    await state.clear()
    await message.answer(
        f"Спасибо, {escape(full_name)}! Данные приняты (кв. {escape(apartment)}).\n"
        f"Ожидайте подтверждения диспетчера — вам придёт уведомление."
    )
    text = (
        f"🆕 <b>Новый житель на подтверждение</b>\n"
        f"{escape(full_name)}, кв. {escape(apartment)}, TG ID: <code>{tid}</code>\n"
        f"Нажмите ниже чтобы одобрить:"
    )
    res = await session.execute(select(User).where(User.role == "dispatcher"))
    for d in res.scalars().all():
        try:
            await bot.send_message(d.telegram_id, text, parse_mode="HTML", reply_markup=approval_keyboard(user.id))
        except Exception:
            logger.exception("registration_notification_failed recipient=%s", d.telegram_id)
    if ADMIN_IDS:
        extra = await session.execute(select(User).where(User.telegram_id.in_(ADMIN_IDS)))
        for u in extra.scalars().all():
            if u.role != "dispatcher":
                try:
                    await bot.send_message(u.telegram_id, text, parse_mode="HTML", reply_markup=approval_keyboard(user.id))
                except Exception:
                    pass


# --- announcements paginated single-message ---

async def build_announcements(session: AsyncSession, page: int, viewer: User) -> tuple[str, InlineKeyboardMarkup]:
    from bot.models import Announcement
    is_disp = _is_dispatcher(viewer)
    total_res = await session.execute(select(func.count()).select_from(Announcement))
    total = total_res.scalar() or 0
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    page = max(0, min(page, total_pages - 1))
    q = await session.execute(select(Announcement).order_by(Announcement.created_at.desc()).limit(PAGE_SIZE).offset(page * PAGE_SIZE))
    anns = q.scalars().all()
    if not anns:
        return "Пока нет объявлений.", InlineKeyboardMarkup(inline_keyboard=[])
    lines = [f"📢 <b>Объявления</b> — стр {page+1}/{total_pages} (всего {total})\nНажмите 📄 чтобы открыть\n"]
    for i, ann in enumerate(anns, 1):
        idx = page * PAGE_SIZE + i
        ts = ann.created_at.strftime("%d.%m %H:%M") if ann.created_at else ""
        preview = escape(ann.text.strip().replace("\n", " "))
        if len(preview) > 80:
            preview = preview[:80] + "…"
        lines.append(f"<b>{idx}.</b> {ts} — {preview}")

    kb_rows: list[list[InlineKeyboardButton]] = []
    # numbered open buttons
    row: list[InlineKeyboardButton] = []
    for i, ann in enumerate(anns, 1):
        idx = page * PAGE_SIZE + i
        row.append(InlineKeyboardButton(text=f"📄 {idx}", callback_data=f"ann_view:{ann.id}:{page}"))
        if len(row) == 3:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)

    if total_pages > 1:
        pag: list[InlineKeyboardButton] = []
        if page > 0:
            pag.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"ann_list:{page-1}"))
        pag.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            pag.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"ann_list:{page+1}"))
        kb_rows.append(pag)

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)

async def build_announcement_detail(session: AsyncSession, ann_id: int, page: int, viewer: User) -> tuple[str, InlineKeyboardMarkup]:
    from bot.models import Announcement
    res = await session.execute(select(Announcement).where(Announcement.id == ann_id))
    ann = res.scalar_one_or_none()
    if not ann:
        return "Объявление не найдено.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"ann_list:{page}")]])
    text = f"📢 <b>Объявление #{ann.id}</b>\n{ann.created_at.strftime('%d.%m %H:%M') if ann.created_at else ''}\n\n{escape(ann.text)}"
    rows: list[list[InlineKeyboardButton]] = []
    if _is_dispatcher(viewer):
        rows.append([InlineKeyboardButton(text="🗑️ Удалить это объявление", callback_data=f"delete_ann:{ann.id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data=f"ann_list:{page}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "📢 Объявления")
async def show_announcements(message: Message, session: AsyncSession):
    result_u = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    viewer = result_u.scalar_one_or_none()
    if not viewer:
        await message.answer("Сначала /start")
        return
    text, kb = await build_announcements(session, page=0, viewer=viewer)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("ann_list:"))
async def ann_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    result_u = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    viewer = result_u.scalar_one_or_none()
    if not viewer:
        await callback.answer("Ошибка", show_alert=True)
        return
    text, kb = await build_announcements(session, page, viewer)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("ann_view:"))
async def ann_view(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    ann_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    result_u = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    viewer = result_u.scalar_one_or_none()
    if not viewer:
        await callback.answer("Ошибка", show_alert=True)
        return
    text, kb = await build_announcement_detail(session, ann_id, page, viewer)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()

# --- delete handlers (requests + announcements) ---

@router.callback_query(F.data.startswith("delete_req:"))
async def confirm_delete_req(callback: CallbackQuery, session: AsyncSession):
    request_id = int(callback.data.split(":")[1])
    from bot.models import Request as ReqModel
    result = await session.execute(select(ReqModel).where(ReqModel.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    # RBAC: resident own-new only, worker none, dispatcher any
    res_u = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    actor = res_u.scalar_one_or_none()
    if not actor:
        await callback.answer("Ошибка", show_alert=True)
        return
    from bot.services.requests import delete_request as _del_check
    # peek check without executing delete: mimic delete_request logic
    from bot.config import is_admin as _is_admin_chk
    is_disp = actor.role == "dispatcher" or (_is_admin_chk(actor.telegram_id) and actor.role == "dispatcher") or (not __import__("bot.config", fromlist=["DEV_MODE"]).DEV_MODE and actor.telegram_id in ADMIN_IDS)
    is_owner = req.resident_id == actor.id
    allowed = is_disp or (is_owner and req.status == "new")
    if not allowed:
        await callback.answer("⛔ Недостаточно прав для удаления", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=confirm_delete_keyboard("req", request_id))
    await callback.answer("Подтвердите удаление")


@router.callback_query(F.data.startswith("delete_ann:"))
async def confirm_delete_ann(callback: CallbackQuery, session: AsyncSession):  # RBAC ann
    from bot.config import is_admin as _is_admin_a2
    ann_id = int(callback.data.split(":")[1])
    res_u2 = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    actor2 = res_u2.scalar_one_or_none()
    if not actor2 or not (actor2.role == "dispatcher" or (_is_admin_a2(actor2.telegram_id) and actor2.role == "dispatcher") or (not __import__("bot.config", fromlist=["DEV_MODE"]).DEV_MODE and actor2.telegram_id in ADMIN_IDS)):
        await callback.answer("⛔ Только диспетчер может удалить объявление", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=confirm_delete_keyboard("ann", ann_id))
    await callback.answer("Подтвердите удаление")


@router.callback_query(F.data.startswith("confirm_delete_req:"))
async def do_delete_req(callback: CallbackQuery, session: AsyncSession):
    request_id = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    actor = result.scalar_one_or_none()
    if not actor:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return
    from bot.services.requests import delete_request
    ok, msg = await delete_request(session, request_id, actor)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return
    await session.commit()
    await callback.message.edit_text(callback.message.text + "\n\n🗑️ Заявка удалена")
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("confirm_delete_ann:"))
async def do_delete_ann(callback: CallbackQuery, session: AsyncSession):
    ann_id = int(callback.data.split(":")[1])
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    actor = result.scalar_one_or_none()
    if not actor:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return
    from bot.services.requests import delete_announcement
    ok, msg = await delete_announcement(session, ann_id, actor)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return
    await session.commit()
    await callback.message.edit_text(callback.message.text + "\n\n🗑️ Объявление удалено")
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("cancel_delete:"))
async def cancel_delete(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отменено")
