from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models import User, Request
from bot.states import RequestStates
from bot.keyboards import category_keyboard, category_keyboard_with_cancel, CATEGORY_LABELS, STATUS_LABELS, resident_menu, resident_request_keyboard, cancel_keyboard
from bot.services.requests import create_request, get_requests_for_resident
from bot.services.llm import get_llm
from bot.config import LLM_DUPLICATE_CONFIDENCE_THRESHOLD
import json
import logging
from bot.services.notify import notify_administrators, notify_workers, notify_dispatchers
from html import escape
from bot.auth import is_approved_owner, is_approved_resident
from bot.callbacks import ResidentRequestCallback
from bot.constants import KAZAKHDOMOFON_TEMPLATES, URGENCY_LABELS
from bot.i18n import category_label, t, text_variants
from bot.timezone import format_local, utc_now
from bot.services.request_routing import (
    APARTMENT_PAID_NOTICE,
    CLEANING_NOTICE,
    next_cleaning_dispatch,
)

router = Router()
logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def _tenant_management_keyboard(
    tenant: User | None, candidates: list[User]
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if tenant:
        rows.append([InlineKeyboardButton(
            text="❌ Отозвать доступ", callback_data=f"tenant_revoke:{tenant.id}"
        )])
    else:
        rows.extend([
            [InlineKeyboardButton(
                text=f"✅ {candidate.full_name or candidate.telegram_id}",
                callback_data=f"tenant_approve:{candidate.id}",
            )]
            for candidate in candidates
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _tenant_management_view(
    session: AsyncSession, owner: User
) -> tuple[str, InlineKeyboardMarkup]:
    active_result = await session.execute(select(User).where(
        User.role == "resident",
        User.resident_subrole == "tenant",
        User.apartment == owner.apartment,
        User.is_approved.is_(True),
    ))
    active = active_result.scalar_one_or_none()
    candidates: list[User] = []
    if active is None:
        candidates_result = await session.execute(select(User).where(
            User.role == "resident",
            User.resident_subrole == "tenant",
            User.apartment == owner.apartment,
            User.is_approved.is_(False),
        ).order_by(User.created_at))
        candidates = list(candidates_result.scalars())
    if active:
        text = (
            "🔑 <b>Арендатор квартиры</b>\n"
            f"{escape(active.full_name or str(active.telegram_id))}\n\n"
            "Можно отозвать доступ и затем одобрить другого арендатора."
        )
    elif candidates:
        text = (
            "🔑 <b>Заявки арендаторов вашей квартиры</b>\n"
            "Можно одобрить одного арендатора:"
        )
    else:
        text = "🔑 Заявок арендаторов для вашей квартиры пока нет."
    return text, _tenant_management_keyboard(active, candidates)


async def _load_owner(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    return user if is_approved_owner(user) else None


@router.message(F.text.in_(text_variants("manage_tenant")))
async def manage_tenant(message: Message, session: AsyncSession):
    owner = await _load_owner(session, message.from_user.id)
    if not owner:
        await message.answer("Эта функция доступна только подтвержденному собственнику.")
        return
    text, markup = await _tenant_management_view(session, owner)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "tenant_manage")
async def manage_tenant_callback(callback: CallbackQuery, session: AsyncSession):
    owner = await _load_owner(session, callback.from_user.id)
    if not owner:
        await callback.answer("Доступно только собственнику", show_alert=True)
        return
    text, markup = await _tenant_management_view(session, owner)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("tenant_approve:"))
async def approve_tenant(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    owner = await _load_owner(session, callback.from_user.id)
    if not owner:
        await callback.answer("Доступно только собственнику", show_alert=True)
        return
    tenant_id = int(callback.data.split(":", 1)[1])
    tenant_result = await session.execute(select(User).where(
        User.id == tenant_id,
        User.role == "resident",
        User.resident_subrole == "tenant",
        User.apartment == owner.apartment,
        User.is_approved.is_(False),
    ))
    tenant = tenant_result.scalar_one_or_none()
    active_result = await session.execute(select(func.count()).select_from(User).where(
        User.role == "resident",
        User.resident_subrole == "tenant",
        User.apartment == owner.apartment,
        User.is_approved.is_(True),
    ))
    if tenant is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if (active_result.scalar() or 0) >= 1:
        await callback.answer("Для квартиры уже одобрен арендатор", show_alert=True)
        return
    tenant.is_approved = True
    tenant.approved_by_owner_id = owner.id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await callback.answer(
            "Для квартиры уже одобрен арендатор", show_alert=True
        )
        return
    try:
        await bot.send_message(
            tenant.telegram_id,
            "✅ Собственник подтвердил ваш доступ. Теперь вы можете пользоваться ботом.",
            reply_markup=resident_menu(tenant.language),
        )
    except Exception:
        logger.exception("tenant_approval_notification_failed tenant_id=%s", tenant.id)
    text, markup = await _tenant_management_view(session, owner)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await callback.answer("Арендатор одобрен")


@router.callback_query(F.data.startswith("tenant_revoke:"))
async def revoke_tenant(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    owner = await _load_owner(session, callback.from_user.id)
    if not owner:
        await callback.answer("Доступно только собственнику", show_alert=True)
        return
    tenant_id = int(callback.data.split(":", 1)[1])
    tenant_result = await session.execute(select(User).where(
        User.id == tenant_id,
        User.apartment == owner.apartment,
        User.resident_subrole == "tenant",
        User.is_approved.is_(True),
    ))
    tenant = tenant_result.scalar_one_or_none()
    if tenant is None:
        await callback.answer("Арендатор не найден", show_alert=True)
        return
    tenant.is_approved = False
    tenant.approved_by_owner_id = None
    await session.commit()
    try:
        await bot.send_message(
            tenant.telegram_id,
            "❌ Собственник отозвал ваш доступ к боту.",
        )
    except Exception:
        logger.exception("tenant_revocation_notification_failed tenant_id=%s", tenant.id)
    text, markup = await _tenant_management_view(session, owner)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await callback.answer("Доступ отозван")

@router.callback_query(F.data == "cancel_fsm")
async def resident_cancel_cb(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    from sqlalchemy import select as _sel
    res = await session.execute(_sel(User).where(User.telegram_id == callback.from_user.id))
    u = res.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    try:
        await callback.message.edit_text(t("cancel", u.language if u else None))
    except Exception:
        pass
    if kb:
        await callback.message.answer(t("main_menu", u.language), reply_markup=kb)
    await callback.answer()

@router.message(F.text.in_(text_variants("cancel")))
async def resident_cancel_text(message: Message, state: FSMContext, session: AsyncSession):
    if await state.get_state() is None:
        return
    await state.clear()
    from bot.handlers.common import get_main_keyboard
    from sqlalchemy import select as _sel
    res = await session.execute(_sel(User).where(User.telegram_id == message.from_user.id))
    u = res.scalar_one_or_none()
    kb = get_main_keyboard(u) if u and u.is_approved else None
    await message.answer(t("cancelled", u.language if u else None), reply_markup=kb)

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
        date = format_local(req.created_at, "%d.%m %H:%M", "")
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
    q = await session.execute(
        select(Request)
        .options(selectinload(Request.attachments))
        .where(Request.id == request_id, Request.resident_id == viewer.id)
    )
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
        f"{'📍 <b>Место:</b> внутри квартиры' + chr(10) if req.service_area == 'apartment' else ''}"
        f"{'📍 <b>Место:</b> МОП / общее имущество' + chr(10) if req.service_area == 'common' else ''}"
        f"{'📎 <b>Вложений:</b> ' + str(len(req.attachments)) + chr(10) if req.attachments else ''}"
        f"🕒 Создана: {format_local(req.created_at, '%d.%m.%Y в %H:%M')}\n"
        f"{('▶️ Принята: ' + format_local(req.accepted_at, '%d.%m.%Y в %H:%M')) if req.accepted_at else ''}\n"
        f"{('✅ Завершена: ' + format_local(req.closed_at, '%d.%m.%Y в %H:%M')) if req.closed_at else ''}"
    )
    if req.completion_result:
        result_label = "выполнена" if req.completion_result == "done" else "не выполнена"
        text += (
            f"\n\n📌 <b>Результат:</b> {result_label}\n"
            f"💬 <b>Комментарий исполнителя:</b> "
            f"{escape(req.completion_comment or '—')}"
        )
    if req.approval_status == "pending":
        text += "\n\n⏳ <b>Ожидает согласования председателя</b>"
    elif req.approval_status == "approved":
        text += "\n\n✅ <b>Согласована председателем</b>"
    elif req.approval_status == "rejected":
        text += "\n\n❌ <b>Отклонена председателем</b>"
    # A resident can only delete their own new request; completion is worker-only.
    rows: list[list[InlineKeyboardButton]] = []
    if req.status == "new" and req.resident_id == viewer.id:
        rows.append([InlineKeyboardButton(text="🗑️ Удалить заявку", callback_data=f"delete_req:{req.id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data=f"res_list:{page}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.in_(text_variants("create_request")))
async def start_request(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not is_approved_resident(user):
        await message.answer(t("finish_registration", user.language if user else None))
        return
    # LLM smart intake: if enabled, allow free-form without picking category first
    try:
        llm = get_llm()
        if llm.enabled:
            await message.answer(
                "Опишите проблему одним сообщением (например: \"течёт кран на кухне, лужа на полу\") —\n"
                "я сам определю категорию и улучшу описание.\n"
                "Или выберите категорию вручную:",
                reply_markup=category_keyboard_with_cancel("req_category", user.language),
            )
            await state.set_state(RequestStates.waiting_description)
            # set_data (not update_data) so a cancelled flow leaves no pending_* behind
            await state.set_data({"category": None, "llm_intake": True})
            return
    except Exception:
        pass
    await message.answer(
        f"📝 <b>{t('new_request', user.language)}</b>\n\n{t('choose_request_category', user.language)}",
        parse_mode="HTML",
        reply_markup=category_keyboard_with_cancel("req_category", user.language),
    )
    await state.set_state(RequestStates.waiting_category)
    await state.set_data({"category": None})


# --- request intake helpers ------------------------------------------------

MIN_DESCRIPTION_LEN = 10
# After this many rejected attempts we let the resident file the заявка as-is,
# so an over-strict model can never lock them out of reporting a real problem.
WEAK_ATTEMPTS_BEFORE_OVERRIDE = 2


def _norm(text: str) -> str:
    """Loose comparison key: is the model's rewrite actually different?"""
    return " ".join((text or "").lower().split())


async def _load_resident(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    return user if is_approved_resident(user) else None


async def _classify(raw: str, category: str | None):
    """Run the LLM scope/quality gate. Returns None when the LLM is unavailable."""
    try:
        llm = get_llm()
        if not llm.enabled:
            return None
        return await llm.classify_and_enrich(raw, category=category)
    except Exception:
        logger.exception("request_classification_failed")
        return None


async def _persist_request(
    session,
    user,
    *,
    category,
    description,
    urgency,
    raw_description,
    meta,
    service_area=None,
    dispatch_after=None,
    attachments=None,
):
    llm_meta = json.dumps(meta, ensure_ascii=False) if meta else None
    req = await create_request(
        session, resident_id=user.id, category=category, description=description,
        urgency=urgency, raw_description=raw_description, llm_meta=llm_meta,
        service_area=service_area, dispatch_after=dispatch_after,
        attachments=attachments,
    )
    await session.commit()
    return req


def _created_text(req: Request, category: str, urgency: str | None, ai: bool) -> str:
    routing = (
        "Заявка ожидает согласования председателя."
        if req.approval_status == "pending"
        else "Заявка будет направлена исполнителю в рабочее время."
        if req.dispatch_after is not None
        else "Мы уведомили подходящих исполнителей."
    )
    return (
        f"✅ <b>Заявка #{req.id} создана</b>\n\n"
        f"{CATEGORY_LABELS[category]}{' • ✨ обработано ИИ' if ai else ''}\n"
        f"{STATUS_LABELS['new']} • {URGENCY_LABELS.get(urgency or 'normal')} приоритет\n\n"
        f"{routing} Статус можно проверить в разделе «📋 Мои заявки»."
    )


async def _notify_new_request(bot: Bot, session: AsyncSession, req: Request, user: User,
                              category: str, description: str) -> None:
    if req.approval_status == "pending":
        await notify_administrators(
            bot, session,
            f"📹 Заявка Казахдомофон #{req.id} ожидает согласования.\n"
            f"Житель: {escape(user.full_name or '')}, кв. {escape(user.apartment or '?')}\n"
            f"{escape(description)}",
        )
        return
    if category == "cleaning":
        await notify_administrators(
            bot, session,
            f"🧹 Новая заявка клининга #{req.id} (просмотр).\n"
            f"Кв. {escape(user.apartment or '?')}: {escape(description[:500])}",
        )
        if req.dispatch_after is not None:
            return
    report = await notify_workers(
        bot,
        session,
        category,
        "",
        urgency=req.urgency,
        message_key="new_request_notification",
        message_values={
            "id": req.id,
            "category": category,
            "address": escape(user.apartment or "?"),
            "resident": escape(user.full_name or ""),
            "description": escape(description[:500]),
        },
    )
    if report.delivered == 0:
        await notify_dispatchers(
            bot,
            session,
            "",
            message_key="no_available_workers",
            message_values={"id": req.id},
        )


def _service_area_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🏠 Внутри квартиры", callback_data="req_area:apartment"
        )],
        [InlineKeyboardButton(
            text="🏢 МОП / общее имущество", callback_data="req_area:common"
        )],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
    ])


def _kazakhdomofon_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"req_kd:{code}")]
        for code, label in KAZAKHDOMOFON_TEMPLATES.items()
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _create_direct_request(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    *,
    user: User,
    category: str,
    description: str,
    service_area: str | None = None,
    dispatch_after=None,
    attachments: list[dict[str, str]] | None = None,
) -> Request:
    req = await _persist_request(
        session,
        user,
        category=category,
        description=description,
        urgency=None,
        raw_description=None,
        meta=None,
        service_area=service_area,
        dispatch_after=dispatch_after,
        attachments=attachments,
    )
    await state.clear()
    await message.answer(
        _created_text(req, category, None, False),
        parse_mode="HTML",
        reply_markup=resident_menu(
            user.language, is_owner=user.resident_subrole == "owner"
        ),
    )
    await _notify_new_request(bot, session, req, user, category, description)
    return req


def _suggestion_keyboard(can_recategorize: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ Создать заявку", callback_data="req_ai:accept")],
        [InlineKeyboardButton(text="📝 Оставить моё описание", callback_data="req_ai:mine")],
    ]
    if can_recategorize:
        rows.append([InlineKeyboardButton(text="🔀 Другая категория", callback_data="req_ai:recat")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("req_category:"))
async def choose_category(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    category = callback.data.split(":")[1]
    user = await _load_resident(session, callback.from_user.id)
    language = user.language if user else None
    if category not in CATEGORY_LABELS:
        await callback.answer(t("unknown_category", language))
        return
    if not user:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return
    data = await state.get_data()
    if category in {"electrician", "plumber"}:
        await state.set_state(RequestStates.waiting_service_area)
        await state.update_data(category=category, llm_intake=False)
        await callback.message.edit_text(
            "Где требуется выполнить работу?",
            reply_markup=_service_area_keyboard(),
        )
        await callback.answer()
        return
    if category == "kazakhdomofon":
        await state.set_state(RequestStates.waiting_category)
        await state.set_data({"category": category})
        await callback.message.edit_text(
            "Выберите тип заявки Казахдомофон:",
            reply_markup=_kazakhdomofon_keyboard(),
        )
        await callback.answer()
        return
    if category == "cleaning":
        await state.set_state(RequestStates.waiting_media)
        await state.set_data({"category": category})
        await callback.message.edit_text(
            "Отправьте фото или видео загрязнения. В подписи обязательно "
            "укажите точное место и описание (минимум 10 символов).",
            reply_markup=cancel_keyboard(user.language),
        )
        await callback.answer()
        return
    pending_raw = data.get("pending_raw")

    # The description was already collected (LLM outage fallback, or "другая
    # категория" from the suggestion card): file it now instead of re-asking.
    if pending_raw:
        if not user:
            await callback.answer("Ошибка пользователя", show_alert=True)
            return
        description = data.get("pending_enriched") or pending_raw
        meta = data.get("pending_meta")
        if meta:
            meta = {**meta, "category_source": "manual"}
        urgency = data.get("pending_urgency")
        req = await _persist_request(
            session, user, category=category, description=description, urgency=urgency,
            raw_description=pending_raw if description != pending_raw else None, meta=meta,
        )
        await state.clear()
        await callback.message.edit_text(
            _created_text(req, category, urgency, bool(meta)), parse_mode="HTML"
        )
        await callback.message.answer(
            "Исполнители на смене получили уведомление.",
            reply_markup=resident_menu(
                user.language, is_owner=user.resident_subrole == "owner"
            ),
        )
        await _notify_new_request(bot, session, req, user, category, description)
        await callback.answer()
        return

    await state.update_data(category=category, llm_intake=False)
    await callback.message.edit_text(
        f"📝 <b>{t('new_request', language)}</b>\n\n"
        f"{t('describe_problem', language)}\n"
        f"{t('category', language)}: {category_label(category, language)}\n\n"
        f"{t('description_hint', language)}",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(RequestStates.waiting_description)
    await callback.answer()


@router.callback_query(RequestStates.waiting_service_area, F.data.startswith("req_area:"))
async def choose_service_area(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
):
    area = callback.data.split(":", 1)[1]
    if area not in {"apartment", "common"}:
        await callback.answer("Неизвестное место", show_alert=True)
        return
    user = await _load_resident(session, callback.from_user.id)
    if not user:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return
    data = await state.get_data()
    await state.update_data(service_area=area)
    pending_raw = data.get("pending_raw")
    if pending_raw:
        category = data.get("category")
        description = data.get("pending_enriched") or pending_raw
        meta = data.get("pending_meta")
        if meta:
            meta = {**meta, "category_source": "manual"}
        urgency = data.get("pending_urgency")
        req = await _persist_request(
            session,
            user,
            category=category,
            description=description,
            urgency=urgency,
            raw_description=pending_raw if description != pending_raw else None,
            meta=meta,
            service_area=area,
        )
        await state.clear()
        await callback.message.edit_text(
            _created_text(req, category, urgency, bool(meta)), parse_mode="HTML"
        )
        await callback.message.answer(
            "Исполнители на смене получили уведомление.",
            reply_markup=resident_menu(
                user.language, is_owner=user.resident_subrole == "owner"
            ),
        )
        await _notify_new_request(
            bot, session, req, user, category, description
        )
        await callback.answer()
        return
    await state.set_state(RequestStates.waiting_description)
    prompt = (
        APARTMENT_PAID_NOTICE
        + "\n\nТеперь опишите требуемую работу и точное место."
        if area == "apartment"
        else "Опишите проблему в МОП и укажите точное место."
    )
    await callback.message.edit_text(
        prompt, parse_mode="HTML", reply_markup=cancel_keyboard(user.language)
    )
    await callback.answer()


@router.callback_query(RequestStates.waiting_category, F.data.startswith("req_kd:"))
async def choose_kazakhdomofon_template(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
):
    template_code = callback.data.split(":", 1)[1]
    description = KAZAKHDOMOFON_TEMPLATES.get(template_code)
    user = await _load_resident(session, callback.from_user.id)
    if not description or not user:
        await callback.answer("Заявка недоступна", show_alert=True)
        return
    if template_code == "face_id":
        await state.set_state(RequestStates.waiting_media)
        await state.set_data({
            "category": "kazakhdomofon",
            "template_code": template_code,
            "description": description,
        })
        await callback.message.edit_text(
            "Отправьте одну фотографию для добавления Face ID.",
            reply_markup=cancel_keyboard(user.language),
        )
        await callback.answer()
        return
    await _create_direct_request(
        callback.message,
        state,
        session,
        bot,
        user=user,
        category="kazakhdomofon",
        description=description,
    )
    await callback.answer()


@router.message(RequestStates.waiting_media, F.photo | F.video)
async def input_request_media(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
):
    data = await state.get_data()
    category = data.get("category")
    user = await _load_resident(session, message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Ошибка пользователя")
        return
    if message.photo:
        media = message.photo[-1]
        attachment = {
            "file_id": media.file_id,
            "file_unique_id": media.file_unique_id,
            "media_type": "photo",
        }
    else:
        media = message.video
        attachment = {
            "file_id": media.file_id,
            "file_unique_id": media.file_unique_id,
            "media_type": "video",
        }
    if category == "kazakhdomofon":
        if attachment["media_type"] != "photo":
            await message.answer("Для Face ID нужна фотография, не видео.")
            return
        await _create_direct_request(
            message,
            state,
            session,
            bot,
            user=user,
            category=category,
            description=data.get("description") or "Добавление Face ID",
            attachments=[attachment],
        )
        return
    if category != "cleaning":
        await state.clear()
        await message.answer("Форма устарела, начните создание заявки заново.")
        return
    description = (message.caption or "").strip()
    if len(description) < MIN_DESCRIPTION_LEN:
        await message.answer(
            "Добавьте к фото или видео подпись с точным местом и описанием "
            "(минимум 10 символов)."
        )
        return
    dispatch_after = next_cleaning_dispatch(utc_now())
    if dispatch_after is not None:
        await message.answer(CLEANING_NOTICE)
    await _create_direct_request(
        message,
        state,
        session,
        bot,
        user=user,
        category=category,
        description=description,
        dispatch_after=dispatch_after,
        attachments=[attachment],
    )


@router.message(RequestStates.waiting_media)
async def require_request_media(message: Message):
    await message.answer("Отправьте фото или видео согласно подсказке выше.")


@router.message(RequestStates.waiting_description, F.text)
async def input_description(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    category = data.get("category")
    user = await _load_resident(session, message.from_user.id)
    language = user.language if user else None
    description_raw = message.text.strip()
    if len(description_raw) < MIN_DESCRIPTION_LEN:
        await message.answer(
            "Опишите проблему подробнее: добавьте место и детали — минимум 10 символов."
        )
        return

    # --- LLM gate: ЖКХ scope check + quality check + rewrite -----------------
    # Runs on both intake paths: free-form (category=None) and manual pick.
    res = await _classify(description_raw, category)

    if res is None:
        # LLM disabled or failed — degrade to the deterministic flow.
        if not category:
            await message.answer(
                "Не удалось определить категорию автоматически. Выберите вручную:",
                reply_markup=category_keyboard_with_cancel("req_category", language),
            )
            await state.update_data(pending_raw=description_raw, pending_enriched=None, pending_meta=None)
            await state.set_state(RequestStates.waiting_category)
            return
        await _finalize_from_message(
            message, state, session, bot, category=category,
            description=description_raw, urgency=None, raw_description=None, meta=None,
        )
        return

    if res.decision == "off_topic":
        # Hard reject: this bot is not a general-purpose assistant.
        await message.answer(
            "🚫 <b>Это не похоже на заявку по дому.</b>\n\n"
            f"{escape(res.follow_up)}\n\n"
            "Опишите проблему по дому — или нажмите «❌ Отмена».",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if res.decision == "needs_detail":
        attempts = int(data.get("weak_attempts", 0)) + 1
        await state.update_data(weak_attempts=attempts, weak_raw=description_raw)
        kb = cancel_keyboard()
        extra = ""
        if attempts >= WEAK_ATTEMPTS_BEFORE_OVERRIDE:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📨 Всё равно отправить", callback_data="req_desc:force")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
            ])
            extra = "\n\nЕсли добавить нечего — отправьте как есть."
        await message.answer(
            f"✏️ <b>Нужно чуть больше деталей.</b>\n\n{escape(res.follow_up)}{extra}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    # --- accepted -----------------------------------------------------------
    from bot.config import LLM_AUTO_CATEGORY_THRESHOLD
    final_category = category or res.category
    if final_category not in CATEGORY_LABELS:
        # Should be impossible (client downgrades accept+no-category), but never
        # let a bad model reply raise KeyError mid-flow.
        logger.warning("llm_accept_without_category reason=%r", res.reason)
        await message.answer(
            "Не удалось определить категорию. Выберите вручную:",
            reply_markup=category_keyboard_with_cancel("req_category"),
        )
        await state.update_data(pending_raw=description_raw, pending_enriched=None, pending_meta=None)
        await state.set_state(RequestStates.waiting_category)
        return
    meta = {
        "category": res.category, "confidence": res.confidence,
        "reason": res.reason, "urgency": res.urgency, "decision": res.decision,
    }
    suggests_rewrite = (
        len(res.enriched) >= MIN_DESCRIPTION_LEN and _norm(res.enriched) != _norm(description_raw)
    )
    category_is_guess = not category
    confident = res.confidence >= LLM_AUTO_CATEGORY_THRESHOLD

    # Nothing to review: the category is settled and the text is unchanged.
    if not suggests_rewrite and (category or confident):
        await _finalize_from_message(
            message, state, session, bot, category=final_category,
            description=description_raw, urgency=res.urgency, raw_description=None,
            meta={**meta, "applied": "original"},
        )
        return

    await state.update_data(
        pending_category=final_category,
        pending_confidence=res.confidence,
        pending_enriched=res.enriched,
        pending_urgency=res.urgency,
        pending_raw=description_raw,
        pending_meta=meta,
    )
    await state.set_state(RequestStates.waiting_confirm)

    cat_line = CATEGORY_LABELS[final_category]
    if category_is_guess:
        cat_line += f" · определено ИИ, уверенность {res.confidence:.0%}"
    body = (
        "✨ <b>Проверьте заявку</b>\n\n"
        f"Категория: {cat_line}\n"
        f"Приоритет: {URGENCY_LABELS.get(res.urgency, res.urgency)}\n"
    )
    if suggests_rewrite:
        body += (
            f"\n<b>Предлагаю описание:</b>\n<i>{escape(res.enriched[:400])}</i>\n"
            f"\n<b>Ваш текст:</b>\n<i>{escape(description_raw[:400])}</i>"
        )
    else:
        body += f"\n<b>Описание:</b>\n<i>{escape(description_raw[:400])}</i>"
    await message.answer(
        body, parse_mode="HTML",
        reply_markup=_suggestion_keyboard(can_recategorize=category_is_guess),
    )


async def _finalize_from_message(message: Message, state: FSMContext, session: AsyncSession, bot: Bot,
                                 *, category, description, urgency, raw_description, meta):
    user = await _load_resident(session, message.from_user.id)
    if not user:
        await message.answer("Ошибка пользователя")
        await state.clear()
        return
    if await _start_duplicate_check(
        message, state, session, user, category=category,
        description=description, urgency=urgency,
        raw_description=raw_description, meta=meta,
    ):
        return
    await _create_checked_request(
        message, state, session, bot, user=user, category=category,
        description=description, urgency=urgency,
        raw_description=raw_description, meta=meta,
    )


async def _duplicate_candidates(session: AsyncSession, user: User, category: str) -> list[dict]:
    """Fetch only recent active candidates; completed requests cannot block filing."""
    result = await session.execute(
        select(Request)
        .where(Request.status.in_(("new", "accepted")))
        .where(Request.category == category)
        .order_by((Request.resident_id == user.id).desc(), Request.created_at.desc())
        .limit(12)
    )
    candidates = []
    for req in result.scalars().all():
        # Never send another resident's private in-apartment description to the
        # model. Cross-resident matching is allowed only for explicit shared
        # building issues, represented by a redacted candidate.
        text = req.description if req.resident_id == user.id else "Общедомовая заявка той же категории"
        candidates.append({
            "id": req.id, "text": text, "same_resident": req.resident_id == user.id,
        })
    return candidates


async def _start_duplicate_check(
    message: Message, state: FSMContext, session: AsyncSession, user: User,
    *, category: str, description: str, urgency, raw_description, meta,
) -> bool:
    state_data = await state.get_data()
    candidates = await _duplicate_candidates(session, user, category)
    if not candidates:
        return False
    try:
        result = await get_llm().check_duplicate(description, category, candidates)
    except Exception:
        # An unavailable advisory check must not prevent a real incident report.
        logger.exception("Initial duplicate check failed")
        return False
    if result.decision == "unique":
        return False
    if result.decision == "duplicate" and result.duplicate_request_id:
        question = (
            f"Заявка #{result.duplicate_request_id} уже открыта. "
            + (result.question or "Это точно та же проблема, или место/объект отличаются?")
        )
    else:
        question = result.question
    await state.update_data(duplicate_draft={
        "category": category, "description": description, "urgency": urgency,
        "raw_description": raw_description, "meta": meta,
        "service_area": state_data.get("service_area"),
        "candidate_ids": [candidate["id"] for candidate in candidates],
    })
    await state.set_state(RequestStates.waiting_duplicate_clarification)
    await message.answer(
        "🔎 <b>Проверка на повторную заявку</b>\n\n" + escape(question),
        parse_mode="HTML", reply_markup=cancel_keyboard(),
    )
    return True


async def _create_checked_request(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot,
    *, user: User, category: str, description: str, urgency, raw_description, meta,
):
    state_data = await state.get_data()
    req = await _persist_request(
        session, user, category=category, description=description,
        urgency=urgency, raw_description=raw_description, meta=meta,
        service_area=state_data.get("service_area"),
    )
    await state.clear()
    await message.answer(
        _created_text(req, category, urgency, bool(meta)),
        parse_mode="HTML",
        reply_markup=resident_menu(
            user.language, is_owner=user.resident_subrole == "owner"
        ),
    )
    await _notify_new_request(bot, session, req, user, category, description)


def _duplicate_decision_keyboard(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Открыть заявку #{request_id}", callback_data=f"req_dup:open:{request_id}")],
        [InlineKeyboardButton(text="➕ Всё равно создать новую", callback_data="req_dup:create")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")],
    ])


@router.message(RequestStates.waiting_duplicate_clarification, F.text)
async def duplicate_clarification(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    draft = data.get("duplicate_draft") or {}
    user = await _load_resident(session, message.from_user.id)
    if not user or not draft:
        await state.clear()
        await message.answer("Проверка устарела, начните создание заявки заново.")
        return
    candidates = await _duplicate_candidates(session, user, draft["category"])
    allowed = set(draft.get("candidate_ids") or [])
    candidates = [candidate for candidate in candidates if candidate["id"] in allowed]
    try:
        result = await get_llm().check_duplicate(
            draft["description"], draft["category"], candidates, clarification=message.text,
        )
    except Exception:
        logger.exception("Duplicate re-check failed")
        result = None
    # A weak or ambiguous result must not reject a potentially new issue.
    if (result is None or result.decision != "duplicate"
            or result.confidence < LLM_DUPLICATE_CONFIDENCE_THRESHOLD
            or result.duplicate_request_id not in allowed):
        meta = draft.get("meta")
        if meta:
            meta = {**meta, "duplicate_check": "unique_or_uncertain"}
        await _create_checked_request(
            message, state, session, bot, user=user, category=draft["category"],
            description=draft["description"], urgency=draft.get("urgency"),
            raw_description=draft.get("raw_description"), meta=meta,
        )
        return
    await state.update_data(duplicate_request_id=result.duplicate_request_id)
    await state.set_state(RequestStates.waiting_duplicate_decision)
    await message.answer(
        f"Похоже, это повтор заявки <b>#{result.duplicate_request_id}</b>. "
        "Чтобы не создавать две одинаковые задачи, новую заявку пока не создаю.\n\n"
        "Если ситуация действительно другая, вы всё равно можете создать новую.",
        parse_mode="HTML", reply_markup=_duplicate_decision_keyboard(result.duplicate_request_id),
    )


@router.callback_query(RequestStates.waiting_duplicate_decision, F.data.startswith("req_dup:"))
async def resolve_duplicate(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    draft = data.get("duplicate_draft") or {}
    duplicate_id = data.get("duplicate_request_id")
    action = callback.data.split(":")[1]
    user = await _load_resident(session, callback.from_user.id)
    if action == "open" and user:
        await state.clear()
        text, kb = await build_resident_detail(session, int(duplicate_id), 0, user)
        if text.startswith("Заявка не найдена"):
            text = (
                f"Заявка <b>#{duplicate_id}</b> уже зарегистрирована как общедомовая. "
                "Чужие персональные данные и описание скрыты."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
        return
    if not user or not draft:
        await callback.answer("Проверка устарела", show_alert=True)
        await state.clear()
        return
    meta = {**(draft.get("meta") or {}), "duplicate_override_of": duplicate_id}
    req = await _persist_request(
        session, user, category=draft["category"], description=draft["description"],
        urgency=draft.get("urgency"), raw_description=draft.get("raw_description"), meta=meta,
        service_area=draft.get("service_area"),
    )
    await state.clear()
    await callback.message.edit_text(
        _created_text(req, draft["category"], draft.get("urgency"), bool(meta)), parse_mode="HTML",
    )
    await callback.message.answer(
        "Исполнители на смене получили уведомление.",
        reply_markup=resident_menu(
            user.language, is_owner=user.resident_subrole == "owner"
        ),
    )
    await _notify_new_request(bot, session, req, user, draft["category"], draft["description"])
    await callback.answer()


@router.callback_query(RequestStates.waiting_description, F.data == "req_desc:force")
async def force_weak_description(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    """Escape hatch: file an under-specified but on-topic заявка verbatim."""
    data = await state.get_data()
    raw = data.get("weak_raw")
    category = data.get("category")
    if not raw:
        await callback.answer("Опишите проблему сообщением", show_alert=True)
        return
    if not category:
        await state.update_data(pending_raw=raw, pending_enriched=None, pending_meta=None)
        await state.set_state(RequestStates.waiting_category)
        await callback.message.edit_text(
            "Выберите категорию:", reply_markup=category_keyboard_with_cancel("req_category")
        )
        await callback.answer()
        return
    user = await _load_resident(session, callback.from_user.id)
    if not user:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return
    await callback.message.edit_text("Проверяю заявку…")
    if not await _start_duplicate_check(
        callback.message, state, session, user, category=category, description=raw,
        urgency=None, raw_description=None, meta=None,
    ):
        await _create_checked_request(
            callback.message, state, session, bot, user=user, category=category,
            description=raw, urgency=None, raw_description=None, meta=None,
        )
    await callback.answer()


@router.callback_query(RequestStates.waiting_confirm, F.data.startswith("req_ai:"))
async def confirm_ai_suggestion(callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot):
    """Resident reviews the LLM's suggestion. State filter kills stale buttons."""
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if choice == "recat":
        await state.update_data(category=None)
        await state.set_state(RequestStates.waiting_category)
        await callback.message.edit_text(
            "Выберите категорию:", reply_markup=category_keyboard_with_cancel("req_category")
        )
        await callback.answer()
        return

    raw = data.get("pending_raw") or ""
    enriched = data.get("pending_enriched") or raw
    category = data.get("pending_category")
    if not category or category not in CATEGORY_LABELS:
        await callback.answer("Категория потерялась, начните заново", show_alert=True)
        await state.clear()
        return

    use_ai = choice == "accept"
    description = enriched if use_ai else raw
    meta = data.get("pending_meta")
    if meta:
        meta = {**meta, "applied": "enriched" if use_ai else "original"}
    user = await _load_resident(session, callback.from_user.id)
    if not user:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return
    urgency = data.get("pending_urgency")
    await callback.message.edit_text("Проверяю заявку…")
    if not await _start_duplicate_check(
        callback.message, state, session, user, category=category,
        description=description, urgency=urgency,
        raw_description=raw if use_ai and description != raw else None, meta=meta,
    ):
        await _create_checked_request(
            callback.message, state, session, bot, user=user, category=category,
            description=description, urgency=urgency,
            raw_description=raw if use_ai and description != raw else None, meta=meta,
        )
    await callback.answer()

@router.message(F.text.in_(text_variants("my_requests")))
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
