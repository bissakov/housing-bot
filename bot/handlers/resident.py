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
from bot.config import LLM_DUPLICATE_CONFIDENCE_THRESHOLD
import json
import logging
from bot.services.notify import notify_workers, notify_dispatchers
from html import escape
from bot.auth import is_approved_resident
from bot.callbacks import ResidentRequestCallback
from bot.constants import URGENCY_LABELS
from bot.i18n import category_label, t
from bot.timezone import format_local

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

@router.message(F.text.in_({"❌ Отмена", "❌ Болдырмау"}))
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
        f"🕒 Создана: {format_local(req.created_at, '%d.%m.%Y в %H:%M')}\n"
        f"{('▶️ Принята: ' + format_local(req.accepted_at, '%d.%m.%Y в %H:%M')) if req.accepted_at else ''}\n"
        f"{('✅ Закрыта: ' + format_local(req.closed_at, '%d.%m.%Y в %H:%M')) if req.closed_at else ''}"
    )
    if req.completion_result:
        result_label = "выполнена" if req.completion_result == "done" else "не выполнена"
        text += (
            f"\n\n📌 <b>Результат:</b> {result_label}\n"
            f"💬 <b>Комментарий исполнителя:</b> "
            f"{escape(req.completion_comment or '—')}"
        )
    # Spec: resident can NOT close; only delete own new. Hide Закрыть for resident.
    rows: list[list[InlineKeyboardButton]] = []
    if req.status == "new" and req.resident_id == viewer.id:
        rows.append([InlineKeyboardButton(text="🗑️ Удалить заявку", callback_data=f"delete_req:{req.id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data=f"res_list:{page}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.in_({"📝 Создать заявку", "📝 Өтінім жасау"}))
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


async def _persist_request(session, user, *, category, description, urgency, raw_description, meta):
    llm_meta = json.dumps(meta, ensure_ascii=False) if meta else None
    req = await create_request(
        session, resident_id=user.id, category=category, description=description,
        urgency=urgency, raw_description=raw_description, llm_meta=llm_meta,
    )
    await session.commit()
    return req


def _created_text(req: Request, category: str, urgency: str | None, ai: bool) -> str:
    return (
        f"✅ <b>Заявка #{req.id} создана</b>\n\n"
        f"{CATEGORY_LABELS[category]}{' • ✨ обработано ИИ' if ai else ''}\n"
        f"{STATUS_LABELS['new']} • {URGENCY_LABELS.get(urgency or 'normal')} приоритет\n\n"
        "Мы уведомили подходящих исполнителей. Статус можно проверить в разделе «📋 Мои заявки»."
    )


async def _notify_new_request(bot: Bot, session: AsyncSession, req: Request, user: User,
                              category: str, description: str) -> None:
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
    data = await state.get_data()
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
        await callback.message.answer("Исполнители на смене получили уведомление.", reply_markup=resident_menu())
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
    """Fetch only recent active candidates; closed requests cannot block filing."""
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
    req = await _persist_request(
        session, user, category=category, description=description,
        urgency=urgency, raw_description=raw_description, meta=meta,
    )
    await state.clear()
    await message.answer(
        _created_text(req, category, urgency, bool(meta)),
        parse_mode="HTML", reply_markup=resident_menu(),
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
    )
    await state.clear()
    await callback.message.edit_text(
        _created_text(req, draft["category"], draft.get("urgency"), bool(meta)), parse_mode="HTML",
    )
    await callback.message.answer("Исполнители на смене получили уведомление.", reply_markup=resident_menu())
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

@router.message(F.text.in_({"📋 Мои заявки", "📋 Менің өтінімдерім"}))
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
