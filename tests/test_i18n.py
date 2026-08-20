import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.handlers.common import cmd_start, set_language
from bot.i18n import category_label, t
from bot.keyboards import reply_cancel_keyboard
from bot.models import User
from tests.conftest import make_callback, make_message


def make_state(user_id=1):
    return FSMContext(storage=MemoryStorage(), key=(123456, user_id, user_id))


def test_kazakh_is_default_language():
    assert t("main_menu", None) == "Басты мәзір"
    assert category_label("security", "kk") == "🛡️ Күзет"
    assert t("main_menu", "ru") == "Главное меню"


def test_schedule_messages_are_localized():
    assert t("schedule_add_hours", "ru") == "➕ Добавить часы"
    assert t("schedule_add_hours", "kk") == "➕ Жұмыс уақытын қосу"
    assert t("schedule_hours_added", "ru") == "✅ Рабочие часы добавлены."
    assert t("schedule_hours_added", "kk") == "✅ Жұмыс уақыты қосылды."


def test_cancel_keyboard_follows_explicit_user_language():
    assert reply_cancel_keyboard("ru").keyboard[0][0].text == "❌ Отмена"
    assert reply_cancel_keyboard("kk").keyboard[0][0].text == "❌ Болдырмау"


@pytest.mark.asyncio
async def test_new_user_selects_language_before_registration(session):
    message = make_message("/start", tg_id=9001)
    state = make_state(9001)

    await cmd_start(message, state, session)

    user = (await session.execute(select(User).where(User.telegram_id == 9001))).scalar_one()
    assert user.language is None
    assert "Тілді таңдаңыз" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_language_callback_saves_preference_and_continues_registration(session):
    user = User(telegram_id=9002, role=None, is_approved=False, language=None)
    session.add(user)
    await session.commit()
    callback = make_callback("set_language:ru", tg_id=9002)
    state = make_state(9002)

    await set_language(callback, state, session)

    await session.refresh(user)
    assert user.language == "ru"
    assert "Язык изменён" in callback.message.edit_text.call_args.args[0]
    assert "Кем вы хотите" in callback.message.answer.call_args.args[0]
