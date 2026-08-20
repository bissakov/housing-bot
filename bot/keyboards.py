from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from bot.constants import CATEGORY_LABELS, STATUS_LABELS
from bot.i18n import DEFAULT_LANGUAGE, category_label, language_choices, normalize_language, t


def language_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, label in language_choices():
        b.button(text=label, callback_data=f"set_language:{code}")
    b.adjust(1)
    return b.as_markup()


def category_keyboard(prefix: str = "req_category", language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat in CATEGORY_LABELS:
        b.button(text=category_label(cat, language), callback_data=f"{prefix}:{cat}")
    b.adjust(1)
    return b.as_markup()


def resident_menu(language: str = DEFAULT_LANGUAGE) -> ReplyKeyboardMarkup:
    language = normalize_language(language)
    b = ReplyKeyboardBuilder()
    labels = tuple(t(key, language) for key in (
        "create_request", "my_requests", "announcements_button", "resident_placeholder"
    ))
    for label in labels[:3]:
        b.button(text=label)
    b.adjust(2, 1)
    return b.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=labels[3],
    )


def worker_menu(is_on_shift: bool, language: str = DEFAULT_LANGUAGE) -> ReplyKeyboardMarkup:
    language = normalize_language(language)
    b = ReplyKeyboardBuilder()
    labels = (
        t("shift_off" if is_on_shift else "shift_on", language),
        t("available_requests", language),
        t("worker_my_requests", language),
        t("announcements_button", language),
        t("worker_placeholder", language),
    )
    for label in labels[:4]:
        b.button(text=label)
    b.adjust(2, 2)
    return b.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=labels[4],
    )


def dispatcher_menu(language: str = DEFAULT_LANGUAGE) -> ReplyKeyboardMarkup:
    language = normalize_language(language)
    b = ReplyKeyboardBuilder()
    labels = tuple(t(key, language) for key in (
        "summary", "all_requests", "pending_workers", "add_worker",
        "worker_schedules", "announcement", "announcements_button",
        "dispatcher_placeholder",
    ))
    for label in labels[:7]:
        b.button(text=label)
    b.adjust(2, 2, 2)
    return b.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=labels[7],
    )


def registration_role_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    language = normalize_language(language)
    b = InlineKeyboardBuilder()
    b.button(text=t("registration_resident", language), callback_data="reg_role:resident")
    b.button(text=t("registration_worker", language), callback_data="reg_role:worker")
    b.button(text=t("cancel", language), callback_data="cancel_fsm")
    b.adjust(1)
    return b.as_markup()


def registration_worker_category_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat in CATEGORY_LABELS:
        b.button(text=category_label(cat, language), callback_data=f"reg_worker_category:{cat}")
    b.button(text=t("cancel", language), callback_data="cancel_fsm")
    b.adjust(1)
    return b.as_markup()

def admin_menu() -> ReplyKeyboardMarkup:
    return dispatcher_menu()


def request_claim_keyboard(request_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Принять", callback_data=f"claim:{request_id}")
    return b.as_markup()


def request_close_keyboard(request_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Выполнено", callback_data=f"complete:{request_id}:done")
    b.button(text="❌ Не выполнено", callback_data=f"complete:{request_id}:not_done")
    b.adjust(2)
    return b.as_markup()


def dispatcher_request_keyboard(
    request_id: int,
    status: str,
    *,
    can_delete: bool = False,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "new":
        b.button(text="👤 Назначить", callback_data=f"assign:{request_id}")
    elif status == "accepted":
        b.button(text="🔄 Переназначить", callback_data=f"reassign:{request_id}")
    if can_delete:
        b.button(text="🗑️ Удалить", callback_data=f"delete_req:{request_id}")
    b.adjust(1)
    return b.as_markup()


def resident_request_keyboard(request_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "new":
        b.button(text="🗑️ Удалить заявку", callback_data=f"delete_req:{request_id}")
    b.adjust(1)
    if not b.buttons:
        return InlineKeyboardMarkup(inline_keyboard=[])
    return b.as_markup()


def announcement_delete_keyboard(ann_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑️ Удалить", callback_data=f"delete_ann:{ann_id}")
    return b.as_markup()


def confirm_delete_keyboard(kind: str, obj_id: int) -> InlineKeyboardMarkup:
    """kind: 'req' or 'ann'"""
    b = InlineKeyboardBuilder()
    if kind == "req":
        b.button(text="✅ Да, удалить", callback_data=f"confirm_delete_req:{obj_id}")
    else:
        b.button(text="✅ Да, удалить", callback_data=f"confirm_delete_ann:{obj_id}")
    b.button(text="❌ Отмена", callback_data=f"cancel_delete:{obj_id}")
    b.adjust(2)
    return b.as_markup()


def assign_worker_keyboard(request_id: int, workers: list) -> InlineKeyboardMarkup:
    """workers: list of User objects with .id, .full_name, .worker_category, .is_on_shift"""
    b = InlineKeyboardBuilder()
    for w in workers:
        shift = "🟢" if w.is_on_shift else "⚪"
        label = f"{shift} {w.full_name or w.telegram_id} ({CATEGORY_LABELS.get(w.worker_category, w.worker_category)})"
        b.button(text=label, callback_data=f"do_assign:{request_id}:{w.id}")
    b.button(text="❌ Отмена", callback_data=f"cancel_assign:{request_id}")
    b.adjust(1)
    return b.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="cancel_fsm")
    return b.as_markup()

def reply_cancel_keyboard(language: str = DEFAULT_LANGUAGE) -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.button(text=t("cancel", language))
    return b.as_markup(resize_keyboard=True, one_time_keyboard=True)

def category_keyboard_with_cancel(prefix: str = "req_category", language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat in CATEGORY_LABELS:
        b.button(text=category_label(cat, language), callback_data=f"{prefix}:{cat}")
    b.button(text=t("cancel", language), callback_data="cancel_fsm")
    b.adjust(1)
    return b.as_markup()

def approval_keyboard(user_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Одобрить", callback_data=f"approve:{user_id}")
    b.button(text="❌ Отклонить", callback_data=f"reject:{user_id}")
    b.adjust(2)
    return b.as_markup()


def pagination_keyboard(prefix: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="◀️ Назад", callback_data=f"{prefix}:page:{page-1}")
    if page < total_pages - 1:
        b.button(text="Вперед ▶️", callback_data=f"{prefix}:page:{page+1}")
    return b.as_markup() if b.buttons else InlineKeyboardMarkup(inline_keyboard=[])
