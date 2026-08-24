"""Development personas and registration reset helpers."""

from dataclasses import dataclass
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import DEV_MODE
from bot.handlers.common import get_main_keyboard
from bot.i18n import normalize_language, t
from bot.models import (
    Announcement,
    DevPersona,
    DevSession,
    Request,
    RequestEvent,
    User,
    WorkerScheduleException,
    WorkerWorkingHour,
)
from bot.services.identity import sync_persona_languages


logger = logging.getLogger(__name__)
router = Router()


@dataclass(frozen=True)
class PersonaSpec:
    key: str
    label_key: str
    full_name: str
    role: str
    index: int
    apartment: str | None = None
    resident_subrole: str | None = None
    worker_category: str | None = None
    is_on_shift: bool = False


PERSONA_SPECS = (
    PersonaSpec(
        "resident_owner", "dev_resident_owner", "Айдана Қасым",
        "resident", 1, apartment="D-01", resident_subrole="owner",
    ),
    PersonaSpec(
        "resident_tenant", "dev_resident_tenant", "Нұрлан Әли",
        "resident", 2, apartment="D-01", resident_subrole="tenant",
    ),
    PersonaSpec(
        "worker_electrician", "dev_worker_electrician", "Әлихан Серік",
        "worker", 3, worker_category="electrician", is_on_shift=True,
    ),
    PersonaSpec(
        "worker_plumber", "dev_worker_plumber", "Мақсат Омар",
        "worker", 4, worker_category="plumber", is_on_shift=True,
    ),
    PersonaSpec(
        "worker_security", "dev_worker_security", "Санжар Бек",
        "worker", 5, worker_category="security", is_on_shift=True,
    ),
    PersonaSpec(
        "worker_cleaning", "dev_worker_cleaning", "Аружан Нұр",
        "worker", 6, worker_category="cleaning", is_on_shift=True,
    ),
    PersonaSpec(
        "worker_kazakhdomofon", "dev_worker_kazakhdomofon", "Данияр Есен",
        "worker", 7, worker_category="kazakhdomofon", is_on_shift=True,
    ),
    PersonaSpec(
        "dispatcher", "dev_dispatcher", "Меруерт Асқар",
        "dispatcher", 8,
    ),
    PersonaSpec(
        "administrator", "dev_administrator", "Ержан Төлеу",
        "administrator", 9,
    ),
)
PERSONAS_BY_KEY = {spec.key: spec for spec in PERSONA_SPECS}


def _synthetic_telegram_id(controller_telegram_id: int, index: int) -> int:
    # Telegram user IDs are positive. A deterministic negative ID keeps each
    # persona stable without ever colliding with a real Telegram account.
    return -(controller_telegram_id * 100 + index)


def _persona_label(spec: PersonaSpec, language: str | None) -> str:
    return t(spec.label_key, language)


def _apply_spec(user: User, spec: PersonaSpec, language: str | None) -> None:
    user.full_name = spec.full_name
    user.apartment = spec.apartment
    user.role = spec.role
    user.resident_subrole = spec.resident_subrole
    user.worker_category = spec.worker_category
    user.language = normalize_language(language)
    user.is_approved = True
    user.approved_by_owner_id = None
    user.is_on_shift = spec.is_on_shift


async def _get_or_create_persona(
    session: AsyncSession,
    controller_telegram_id: int,
    spec: PersonaSpec,
    language: str | None,
) -> DevPersona:
    row = await session.execute(
        select(DevPersona, User)
        .join(User, User.id == DevPersona.user_id)
        .where(
            DevPersona.controller_telegram_id == controller_telegram_id,
            DevPersona.persona_key == spec.key,
        )
    )
    existing = row.one_or_none()
    if existing is not None:
        persona, user = existing
        _apply_spec(user, spec, language)
        return persona

    user = User(telegram_id=_synthetic_telegram_id(controller_telegram_id, spec.index))
    _apply_spec(user, spec, language)
    session.add(user)
    await session.flush()
    persona = DevPersona(
        controller_telegram_id=controller_telegram_id,
        persona_key=spec.key,
        user_id=user.id,
    )
    session.add(persona)
    await session.flush()
    return persona


async def _active_persona(
    session: AsyncSession, controller_telegram_id: int
) -> tuple[DevPersona, User] | None:
    row = await session.execute(
        select(DevPersona, User)
        .join(DevSession, DevSession.persona_id == DevPersona.id)
        .join(User, User.id == DevPersona.user_id)
        .where(DevSession.controller_telegram_id == controller_telegram_id)
    )
    return row.one_or_none()


async def _deactivate(session: AsyncSession, controller_telegram_id: int) -> None:
    await session.execute(
        delete(DevSession).where(
            DevSession.controller_telegram_id == controller_telegram_id
        )
    )


def dev_keyboard(current: str | None, language: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    own_prefix = "✅ " if current is None else ""
    builder.button(
        text=f"{own_prefix}👤 {t('dev_own_profile', language)}",
        callback_data="dev_switch:self",
    )
    for spec in PERSONA_SPECS:
        prefix = "✅ " if spec.key == current else ""
        builder.button(
            text=f"{prefix}{_persona_label(spec, language)}",
            callback_data=f"dev_switch:{spec.key}",
        )
    builder.adjust(1, 2, 2, 2, 2, 1)
    if current is not None:
        rows = list(builder.export())
        rows.append([InlineKeyboardButton(
            text=t("dev_clear_persona", language),
            callback_data="dev_persona_reset",
        )])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return builder.as_markup()


@router.message(F.text == "/dev")
async def dev_entry(message: Message, state: FSMContext, session: AsyncSession):
    if not DEV_MODE:
        await message.answer(t("dev_disabled", message.from_user.language_code))
        return
    await state.clear()
    real_user = await session.scalar(
        select(User).where(User.telegram_id == message.from_user.id)
    )
    if real_user is None:
        await message.answer(t("dev_start_first", message.from_user.language_code))
        return
    language = normalize_language(real_user.language)
    await sync_persona_languages(session, message.from_user.id, language)
    await session.commit()
    active = await _active_persona(session, message.from_user.id)
    current = active[0].persona_key if active else None
    current_label = (
        _persona_label(PERSONAS_BY_KEY[current], language)
        if current else t("dev_own_profile", language)
    )
    await message.answer(
        t("dev_menu", language, label=current_label),
        parse_mode="HTML",
        reply_markup=dev_keyboard(current, language),
    )


@router.callback_query(F.data.startswith("dev_switch:"))
async def dev_switch(callback: CallbackQuery, session: AsyncSession):
    if not DEV_MODE:
        await callback.answer(
            t("dev_disabled", callback.from_user.language_code), show_alert=True
        )
        return
    controller_id = callback.from_user.id
    controller = await session.scalar(
        select(User).where(User.telegram_id == controller_id)
    )
    if controller is None:
        await callback.answer(
            t("dev_start_first", callback.from_user.language_code), show_alert=True
        )
        return
    language = normalize_language(controller.language)

    choice = callback.data.split(":", 1)[1]
    if choice == "self":
        await _deactivate(session, controller_id)
        await session.commit()
        user = controller
        label = t("dev_own_profile", language)
        current = None
    else:
        spec = PERSONAS_BY_KEY.get(choice)
        if spec is None:
            await callback.answer(t("dev_unknown_persona", language), show_alert=True)
            return
        persona = await _get_or_create_persona(
            session, controller_id, spec, language
        )
        dev_session = await session.get(DevSession, controller_id)
        if dev_session is None:
            session.add(DevSession(
                controller_telegram_id=controller_id, persona_id=persona.id
            ))
        else:
            dev_session.persona_id = persona.id
        await session.commit()
        user = await session.get(User, persona.user_id)
        label = _persona_label(spec, language)
        current = spec.key

    await callback.message.edit_text(
        t("dev_active_persona", language, label=label),
        parse_mode="HTML",
        reply_markup=dev_keyboard(current, language),
    )
    if user is not None:
        await callback.message.answer(
            f"{t('main_menu', language)} · {label}",
            reply_markup=get_main_keyboard(user),
        )
    await callback.answer(t("dev_persona_switched", language))


async def _clear_persona_data(session: AsyncSession, persona_user_id: int) -> None:
    await session.execute(
        update(Request)
        .where(
            Request.worker_id == persona_user_id,
            Request.resident_id != persona_user_id,
        )
        .values(worker_id=None, status="new", accepted_at=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(Request)
        .where(Request.resident_id == persona_user_id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(Request)
        .where(Request.reviewed_by_id == persona_user_id)
        .values(reviewed_by_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(Announcement).where(Announcement.author_id == persona_user_id)
    )
    await session.execute(
        update(RequestEvent)
        .where(RequestEvent.actor_id == persona_user_id)
        .values(actor_id=None)
    )
    await session.execute(
        delete(WorkerWorkingHour).where(
            WorkerWorkingHour.worker_id == persona_user_id
        )
    )
    await session.execute(
        delete(WorkerScheduleException).where(
            WorkerScheduleException.worker_id == persona_user_id
        )
    )
    # Core deletes bypass ORM identity-map bookkeeping. Expire cached request
    # objects so later actions in the same update cannot observe deleted data.
    session.expire_all()


@router.callback_query(F.data == "dev_persona_reset")
async def dev_persona_reset(callback: CallbackQuery, session: AsyncSession):
    if not DEV_MODE:
        await callback.answer(
            t("dev_disabled", callback.from_user.language_code), show_alert=True
        )
        return
    controller = await session.scalar(
        select(User).where(User.telegram_id == callback.from_user.id)
    )
    language = normalize_language(controller.language if controller else None)
    active = await _active_persona(session, callback.from_user.id)
    if active is None:
        await callback.answer(t("dev_no_active_persona", language), show_alert=True)
        return
    persona, user = active
    spec = PERSONAS_BY_KEY[persona.persona_key]
    await _clear_persona_data(session, user.id)
    _apply_spec(user, spec, language)
    await session.commit()
    await callback.answer(t("dev_persona_cleared", language), show_alert=True)


@router.message(F.text == "/reset")
async def dev_reset(message: Message, state: FSMContext, session: AsyncSession):
    if not DEV_MODE:
        await message.answer(t("dev_disabled", message.from_user.language_code))
        return
    await state.clear()
    # Registration reset always targets the developer's real profile.
    await _deactivate(session, message.from_user.id)
    user = await session.scalar(
        select(User).where(User.telegram_id == message.from_user.id)
    )
    if user is None:
        await session.commit()
        await message.answer(t("dev_profile_missing", message.from_user.language_code))
        return
    language = normalize_language(user.language)
    uid = user.id
    try:
        await _clear_persona_data(session, uid)
        session.expunge_all()
        await session.execute(
            delete(User)
            .where(User.id == uid)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    except Exception:
        logger.exception("dev_reset failed for user_id=%s", uid)
        await session.rollback()
        await _deactivate(session, message.from_user.id)
        user = await session.scalar(select(User).where(User.id == uid))
        if user is None:
            await session.commit()
            await message.answer(t("dev_profile_deleted", language))
            return
        user.full_name = None
        user.apartment = None
        user.worker_category = None
        user.resident_subrole = None
        user.role = "resident"
        user.is_approved = False
        user.is_on_shift = False
        await session.commit()
        await message.answer(t("dev_profile_reset", language))
        return
    await message.answer(t("dev_profile_deleted", language))
