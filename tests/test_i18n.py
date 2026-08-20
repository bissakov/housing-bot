import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.handlers.common import cmd_start, set_language
from bot.i18n import SUPPORTED_LANGUAGES, TEXTS, category_label, normalize_language, t, text_variants
from bot.keyboards import dispatcher_menu, resident_menu, worker_menu
from bot.keyboards import reply_cancel_keyboard
from bot.models import User
from tests.conftest import make_callback, make_message


def make_state(user_id=1):
    return FSMContext(storage=MemoryStorage(), key=(123456, user_id, user_id))


def test_kazakh_is_default_language():
    assert t("main_menu", None) == "Басты мәзір"
    assert category_label("security", "kk") == "🛡️ Күзет"
    assert t("main_menu", "ru") == "Главное меню"


def test_regional_language_codes_are_normalized():
    assert normalize_language("ru-RU") == "ru"
    assert normalize_language("kk_KZ") == "kk"


def test_every_message_is_translated_into_every_supported_language():
    assert TEXTS
    assert all(set(translations) == SUPPORTED_LANGUAGES for translations in TEXTS.values())


def test_text_variants_come_from_the_catalogue():
    assert text_variants("cancel") == frozenset({"❌ Болдырмау", "❌ Отмена"})


def test_each_menu_uses_only_the_requested_language():
    resident_texts = {button.text for row in resident_menu("kk").keyboard for button in row}
    worker_texts = {button.text for row in worker_menu(False, "ru").keyboard for button in row}
    dispatcher_texts = {button.text for row in dispatcher_menu("kk").keyboard for button in row}

    assert "📝 Өтінім жасау" in resident_texts
    assert "📝 Создать заявку" not in resident_texts
    assert "▶️ На смену" in worker_texts
    assert "▶️ Ауысымға шығу" not in worker_texts
    assert "📊 Жиынтық" in dispatcher_texts
    assert "📊 Сводка" not in dispatcher_texts


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
