from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from bot.constants import CATEGORY_LABELS, STATUS_LABELS


def category_keyboard(prefix: str = "req_category") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat, label in CATEGORY_LABELS.items():
        b.button(text=label, callback_data=f"{prefix}:{cat}")
    b.adjust(1)
    return b.as_markup()


def resident_menu() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.button(text="📝 Создать заявку")
    b.button(text="📋 Мои заявки")
    b.button(text="📢 Объявления")
    b.adjust(2, 1)
    return b.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def worker_menu(is_on_shift: bool) -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    shift_text = "⏸️ Уйти со смены" if is_on_shift else "▶️ На смену"
    b.button(text=shift_text)
    b.button(text="📋 Доступные заявки")
    b.button(text="🔧 Мои заявки")
    b.button(text="📢 Объявления")
    b.adjust(2, 2)
    return b.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Меню исполнителя",
    )


def dispatcher_menu() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.button(text="📊 Сводка")
    b.button(text="📋 Все заявки")
    b.button(text="⏳ На подтверждение")
    b.button(text="➕ Добавить исполнителя")
    b.button(text="📢 Создать объявление")
    b.button(text="📢 Объявления")
    b.adjust(2, 2, 2)
    return b.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Панель диспетчера",
    )


def registration_role_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🏠 Я житель", callback_data="reg_role:resident")
    b.button(text="🔧 Я исполнитель", callback_data="reg_role:worker")
    b.button(text="❌ Отмена", callback_data="cancel_fsm")
    b.adjust(1)
    return b.as_markup()


def registration_worker_category_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat, label in CATEGORY_LABELS.items():
        b.button(text=label, callback_data=f"reg_worker_category:{cat}")
    b.button(text="❌ Отмена", callback_data="cancel_fsm")
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
    b.button(text="✅ Закрыть", callback_data=f"close:{request_id}")
    return b.as_markup()


def dispatcher_request_keyboard(request_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "new":
        b.button(text="👤 Назначить", callback_data=f"assign:{request_id}")
    elif status == "accepted":
        b.button(text="🔄 Переназначить", callback_data=f"reassign:{request_id}")
        b.button(text="✅ Закрыть", callback_data=f"close:{request_id}")
    b.button(text="🗑️ Удалить", callback_data=f"delete_req:{request_id}")
    b.adjust(1)
    return b.as_markup()


def resident_request_keyboard(request_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "new":
        b.button(text="🗑️ Удалить заявку", callback_data=f"delete_req:{request_id}")
    elif status == "accepted":
        b.button(text="✅ Закрыть", callback_data=f"close:{request_id}")
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

def reply_cancel_keyboard() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.button(text="❌ Отмена")
    return b.as_markup(resize_keyboard=True, one_time_keyboard=True)

def category_keyboard_with_cancel(prefix: str = "req_category") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat, label in CATEGORY_LABELS.items():
        b.button(text=label, callback_data=f"{prefix}:{cat}")
    b.button(text="❌ Отмена", callback_data="cancel_fsm")
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
