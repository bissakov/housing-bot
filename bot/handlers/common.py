from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from html import escape
import logging

from bot.models import User
from bot.auth import is_administrator, is_dispatcher
from bot.config import ADMIN_IDS
from bot.constants import CATEGORY_LABELS, REQUEST_CATEGORIES
from bot.states import RegistrationStates
from bot.services.notify import notify_dispatchers
from bot.i18n import SUPPORTED_LANGUAGES, t, text_variants
from bot.keyboards import (
    resident_menu, worker_menu, dispatcher_menu, confirm_delete_keyboard,
    approval_keyboard, cancel_keyboard, reply_cancel_keyboard, language_keyboard,
    registration_resident_subrole_keyboard,
)
from bot.timezone import format_local

router = Router()
logger = logging.getLogger(__name__)

PAGE_SIZE = 5

def get_main_keyboard(user: User):
    if is_dispatcher(user):
        return dispatcher_menu(
            user.language, chairman=is_administrator(user)
        )
    if user.role == "worker":
        return worker_menu(user.is_on_shift, user.language)
    return resident_menu(
        user.language, is_owner=user.resident_subrole == "owner"
    )


def _is_dispatcher(user: User) -> bool:
    return is_dispatcher(user)


@router.callback_query(F.data == "cancel_fsm")
async def cancel_fsm_cb(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    u = result.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await callback.message.edit_text(t("cancel", u.language if u else None))
    if kb:
        await callback.message.answer(t("main_menu", u.language), reply_markup=kb)
    await callback.answer()

@router.message(F.text.in_(text_variants("cancel")))
async def cancel_fsm_text(message: Message, state: FSMContext, session: AsyncSession):
    cur = await state.get_state()
    if cur is None:
        return
    await state.clear()
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    u = result.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await message.answer(t("cancelled", u.language if u else None), reply_markup=kb)

async def ensure_user(message: Message, session: AsyncSession) -> User:  # type: ignore[no-redef]
    tid = message.from_user.id
    result = await session.execute(select(User).where(User.telegram_id == tid))
    user = result.scalar_one_or_none()
    if user:
        if tid in ADMIN_IDS and user.role != "administrator":
            user.role = "administrator"
            user.is_approved = True
            await session.flush()
        return user
    is_admin = tid in ADMIN_IDS
    role = "administrator" if is_admin else "resident"
    user = User(telegram_id=tid, role=role, is_approved=is_admin)
    session.add(user)
    await session.flush()
    return user


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    user = await ensure_user(message, session)
    await session.commit()

    if user.language is None:
        await state.set_state(RegistrationStates.waiting_language)
        await message.answer(t("choose_language", "kk"), reply_markup=language_keyboard())
        return

    await _show_start(message, state, session, user)


async def _show_start(message: Message, state: FSMContext, session: AsyncSession, user: User):

    # DEV helper hint: /dev switches instantly; /reset does fresh picker. No auto-re-picker on /start for approved users by design.

    # Fresh user: offer resident vs worker self-registration before any text prompts
    if not user.is_approved and not user.full_name and not user.apartment and user.worker_category is None:
        from bot.keyboards import registration_role_keyboard
        await state.set_state(RegistrationStates.waiting_role)
        await message.answer(
            t("welcome_role", user.language),
            parse_mode="HTML",
            reply_markup=registration_role_keyboard(user.language),
        )
        return
    if (
        not user.is_approved
        and user.role == "resident"
        and user.resident_subrole is None
    ):
        await state.update_data(reg_role="resident")
        await message.answer(
            t("choose_resident_subrole", user.language),
            reply_markup=registration_resident_subrole_keyboard(user.language),
        )
        await state.set_state(RegistrationStates.waiting_resident_subrole)
        return
    if not user.is_approved and user.role in ("resident", "worker") and not user.full_name:
        # Half-finished registration (bot restarted, FSM lost): resume at step 1 for either role.
        await state.update_data(reg_role=user.role)
        await message.answer(
            t("welcome_name", user.language),
            parse_mode="HTML",
            reply_markup=reply_cancel_keyboard(user.language)
        )
        await state.set_state(RegistrationStates.waiting_name)
        return

    if not user.is_approved:
        if user.role == "worker":
            await message.answer(t("waiting_worker", user.language))
        elif user.resident_subrole == "tenant":
            await message.answer(t("waiting_tenant", user.language))
        else:
            await message.answer(t("waiting_resident", user.language))
        return

    kb = get_main_keyboard(user)
    role_label = t(f"role_{user.role}", user.language)
    pending_note = ""
    if _is_dispatcher(user):
        pend = await session.execute(select(User).where(
            User.is_approved.is_(False),
            ~((User.role == "resident") & (User.resident_subrole == "tenant")),
        ))
        cnt = len(pend.scalars().all())
        if cnt:
            pending_note = f"\n⏳ {t('pending_approval', user.language)}: {cnt}"
    name = escape(user.full_name) if user.full_name else role_label
    await message.answer(
        f"👋 <b>{name}</b>\n"
        f"{t('role', user.language)}: {role_label}{pending_note}\n\n"
        f"{t('main_prompt', user.language)}",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(Command("language"))
async def cmd_language(message: Message, state: FSMContext, session: AsyncSession):
    await ensure_user(message, session)
    await state.clear()
    await message.answer(t("choose_language", "kk"), reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("set_language:"))
async def set_language(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    language = callback.data.split(":", 1)[1]
    if language not in SUPPORTED_LANGUAGES:
        await callback.answer()
        return
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
        )
        session.add(user)
        await session.flush()
    was_initial = user.language is None
    user.language = language
    await session.commit()
    await callback.message.edit_text(t("language_changed", language))
    await callback.answer()
    if was_initial:
        await _show_start(callback.message, state, session, user)
    elif user.is_approved:
        await callback.message.answer(t("main_menu", language), reply_markup=get_main_keyboard(user))


@router.callback_query(F.data.startswith("reg_role:"))
async def reg_role_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if await state.get_state() != RegistrationStates.waiting_role:
        await callback.answer()
        return
    choice = callback.data.split(":", 1)[1]
    result = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    u = result.scalar_one_or_none()
    language = u.language if u else None
    if choice == "worker":
        if u:
            u.role = "worker"
            u.resident_subrole = None
            u.approved_by_owner_id = None
            await session.commit()
        # Workers give their name first, same as residents — dispatchers approve a person, not a TG id.
        await state.update_data(reg_role="worker")
        await callback.message.edit_text(t("step_name", language))
        await callback.message.answer(t("enter_name", language), reply_markup=reply_cancel_keyboard(language))
        await state.set_state(RegistrationStates.waiting_name)
        await callback.answer()
        return
    if u:
        u.role = "resident"
        u.resident_subrole = None
        u.approved_by_owner_id = None
        await session.commit()
    await state.update_data(reg_role="resident")
    await callback.message.edit_text(
        t("choose_resident_subrole", language),
        reply_markup=registration_resident_subrole_keyboard(language)
    )
    await state.set_state(RegistrationStates.waiting_resident_subrole)
    await callback.answer()


@router.callback_query(
    RegistrationStates.waiting_resident_subrole,
    F.data.startswith("reg_resident_subrole:"),
)
async def reg_resident_subrole(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    subrole = callback.data.split(":", 1)[1]
    if subrole not in {"owner", "tenant"}:
        await callback.answer(t("registration_error"), show_alert=True)
        return
    result = await session.execute(
        select(User).where(User.telegram_id == callback.from_user.id)
    )
    user = result.scalar_one_or_none()
    if not user:
        await callback.answer(t("start_first"), show_alert=True)
        await state.clear()
        return
    user.role = "resident"
    user.resident_subrole = subrole
    user.approved_by_owner_id = None
    await session.commit()
    await state.update_data(reg_role="resident")
    await callback.message.edit_text(t(f"subrole_{subrole}", user.language))
    await callback.message.answer(
        t("enter_name", user.language),
        reply_markup=reply_cancel_keyboard(user.language),
    )
    await state.set_state(RegistrationStates.waiting_name)
    await callback.answer()


@router.callback_query(F.data.startswith("reg_worker_category:"))
async def reg_worker_category_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    if await state.get_state() != RegistrationStates.waiting_worker_category:
        await callback.answer()
        return
    cat = callback.data.split(":", 1)[1]
    if cat not in REQUEST_CATEGORIES:
        await callback.answer("Неизвестная категория", show_alert=True)
        return
    tid = callback.from_user.id
    result = await session.execute(select(User).where(User.telegram_id == tid))
    user = result.scalar_one_or_none()
    language = user.language if user else None
    if not user:
        await callback.answer(t("start_first", language), show_alert=True)
        await state.clear()
        return
    data = await state.get_data()
    full_name = (data.get("full_name") or user.full_name or "").strip()
    if not full_name:
        # No name collected (stale FSM after a restart) — send them back to step 1.
        await callback.message.edit_text(t("step_name", language))
        await callback.message.answer(t("enter_name", language), reply_markup=reply_cancel_keyboard(language))
        await state.update_data(reg_role="worker", worker_category=cat)
        await state.set_state(RegistrationStates.waiting_name)
        await callback.answer()
        return
    user.full_name = full_name
    user.worker_category = cat
    user.role = "worker"
    user.is_approved = False
    await session.commit()
    await state.clear()
    label = CATEGORY_LABELS.get(cat, cat)
    await callback.message.edit_text(
        f"Спасибо, {escape(full_name)}! Дисциплина: <b>{label}</b>\n"
        "⏳ Ожидайте подтверждения диспетчера — вам придёт уведомление.",
        parse_mode="HTML",
    )
    await callback.answer()
    # notify dispatchers reusing approval_keyboard
    text = (
        f"🆕 <b>Новый исполнитель на подтверждение</b>\n"
        f"{escape(full_name)}, категория: {label}, TG ID: <code>{tid}</code>\n"
        f"Нажмите ниже чтобы подтвердить:"
    )
    report = await notify_dispatchers(
        bot, session, text, parse_mode="HTML", reply_markup=approval_keyboard(user.id)
    )
    logger.info(
        "worker_registration_notified user_id=%s delivered=%s failed=%s",
        user.id, report.delivered, report.failed,
    )


@router.message(RegistrationStates.waiting_name, F.text)
async def reg_name(message: Message, state: FSMContext, session: AsyncSession):
    name = message.text.strip()
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    language = user.language if user else None
    if len(name) < 3:
        await message.answer(t("invalid_name", language))
        return
    await state.update_data(full_name=name)
    data = await state.get_data()
    if data.get("reg_role") == "worker":
        from bot.keyboards import registration_worker_category_keyboard
        await message.answer(
            t("step_worker_category", language),
            reply_markup=registration_worker_category_keyboard(language),
        )
        await state.set_state(RegistrationStates.waiting_worker_category)
        return
    await message.answer(
        t("step_apartment", language), reply_markup=reply_cancel_keyboard(language)
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
        await message.answer(t("registration_error", None))
        await state.clear()
        return
    user.full_name = full_name
    user.apartment = apartment
    user.is_approved = False
    await session.commit()
    await state.clear()
    completion_key = "tenant_registration_done" if user.resident_subrole == "tenant" else "registration_done"
    await message.answer(t(
        completion_key, user.language,
        name=escape(full_name), apartment=escape(apartment),
    ))
    if user.resident_subrole == "tenant":
        owners_result = await session.execute(select(User).where(
            User.role == "resident",
            User.resident_subrole == "owner",
            User.apartment == apartment,
            User.is_approved.is_(True),
        ))
        notification = (
            f"🔑 <b>Арендатор ожидает подтверждения</b>\n"
            f"{escape(full_name)}, кв. {escape(apartment)}"
        )
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="🔑 Управление арендатором", callback_data="tenant_manage"
        )]])
        for owner in owners_result.scalars():
            try:
                await bot.send_message(owner.telegram_id, notification, parse_mode="HTML", reply_markup=markup)
            except Exception:
                logger.exception("tenant_registration_owner_notification_failed owner_id=%s", owner.id)
        return
    text = (
        f"🆕 <b>Новый собственник на подтверждение</b>\n"
        f"{escape(full_name)}, кв. {escape(apartment)}, TG ID: <code>{tid}</code>\n"
        f"Нажмите ниже чтобы одобрить:"
    )
    report = await notify_dispatchers(
        bot, session, text, parse_mode="HTML", reply_markup=approval_keyboard(user.id)
    )
    logger.info(
        "resident_registration_notified user_id=%s delivered=%s failed=%s",
        user.id, report.delivered, report.failed,
    )


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
        return t("no_announcements", viewer.language), InlineKeyboardMarkup(inline_keyboard=[])
    if viewer.language == "kk":
        heading = f"📢 <b>{t('announcements', viewer.language)}</b> — {page+1}/{total_pages} (барлығы {total})\nАшу үшін 📄 басыңыз\n"
    else:
        heading = f"📢 <b>{t('announcements', viewer.language)}</b> — стр {page+1}/{total_pages} (всего {total})\nНажмите 📄 чтобы открыть\n"
    lines = [heading]
    for i, ann in enumerate(anns, 1):
        idx = page * PAGE_SIZE + i
        ts = format_local(ann.created_at, "%d.%m %H:%M", "")
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
        return t("not_found_announcement", viewer.language), InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("back", viewer.language), callback_data=f"ann_list:{page}")]])
    text = f"📢 <b>{t('announcement', viewer.language)} #{ann.id}</b>\n{format_local(ann.created_at, '%d.%m %H:%M', '')}\n\n{escape(ann.text)}"
    rows: list[list[InlineKeyboardButton]] = []
    if is_administrator(viewer):
        rows.append([InlineKeyboardButton(text="🗑️ Удалить это объявление", callback_data=f"delete_ann:{ann.id}")])
    rows.append([InlineKeyboardButton(text=t("back_to_list", viewer.language), callback_data=f"ann_list:{page}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.in_(text_variants("announcements_button")))
async def show_announcements(message: Message, session: AsyncSession):
    result_u = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    viewer = result_u.scalar_one_or_none()
    if not viewer:
        await message.answer(t("start_first", None))
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
    # RBAC: resident own-new only, worker/dispatcher none, administrator any.
    res_u = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    actor = res_u.scalar_one_or_none()
    if not actor:
        await callback.answer("Ошибка", show_alert=True)
        return
    is_owner = req.resident_id == actor.id
    allowed = is_administrator(actor) or (is_owner and req.status == "new")
    if not allowed:
        await callback.answer("⛔ Недостаточно прав для удаления", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=confirm_delete_keyboard("req", request_id))
    await callback.answer("Подтвердите удаление")


@router.callback_query(F.data.startswith("delete_ann:"))
async def confirm_delete_ann(callback: CallbackQuery, session: AsyncSession):  # RBAC ann
    ann_id = int(callback.data.split(":")[1])
    res_u2 = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    actor2 = res_u2.scalar_one_or_none()
    if not is_administrator(actor2):
        await callback.answer("⛔ Только администратор может удалить объявление", show_alert=True)
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
