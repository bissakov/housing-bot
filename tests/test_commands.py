from unittest.mock import AsyncMock

import pytest
from aiogram.types import BotCommandScopeChat

from bot.commands import command_menu, register_global_command_menus
from bot.handlers.common import cmd_start, set_language
from bot.models import User
from tests.conftest import make_callback


def test_command_menu_uses_one_language_consistently():
    russian = command_menu("ru", include_dev=True)
    kazakh = command_menu("kk", include_dev=True)

    assert [command.description for command in russian] == [
        "Регистрация",
        "Изменить язык",
        "Выбрать постоянную тестовую персону",
        "Удалить профиль и заново проверить регистрацию",
    ]
    assert [command.description for command in kazakh] == [
        "Тіркелу",
        "Тілді өзгерту",
        "Тұрақты тест персонасын таңдау",
        "Профильді жойып, тіркелуді қайта тексеру",
    ]


@pytest.mark.asyncio
async def test_global_command_menus_are_registered_for_each_language(fake_bot):
    await register_global_command_menus(fake_bot, include_dev=False)

    assert fake_bot.set_my_commands.await_count == 3
    language_codes = {
        call.kwargs.get("language_code")
        for call in fake_bot.set_my_commands.await_args_list
    }
    assert language_codes == {None, "kk", "ru"}


@pytest.mark.asyncio
async def test_language_choice_updates_chat_command_menu(session, fake_bot):
    user = User(telegram_id=9100, role="resident", is_approved=False)
    session.add(user)
    await session.commit()
    callback = make_callback("set_language:ru", tg_id=9100)

    await set_language(callback, AsyncMock(), session, fake_bot)

    fake_bot.set_my_commands.assert_awaited_once()
    commands = fake_bot.set_my_commands.await_args.args[0]
    scope = fake_bot.set_my_commands.await_args.kwargs["scope"]
    assert isinstance(scope, BotCommandScopeChat)
    assert scope.chat_id == 9100
    assert all("Тіркелу" not in command.description for command in commands)
    assert commands[0].description == "Регистрация"


@pytest.mark.asyncio
async def test_start_restores_saved_language_in_chat_command_menu(session, fake_bot):
    user = User(
        telegram_id=9101,
        role="resident",
        is_approved=True,
        language="ru",
        full_name="Тестовый пользователь",
        apartment="1",
    )
    session.add(user)
    await session.commit()
    message = make_callback("unused", tg_id=9101).message

    await cmd_start(message, AsyncMock(), session, fake_bot)

    commands = fake_bot.set_my_commands.await_args.args[0]
    assert commands[0].description == "Регистрация"
    assert fake_bot.set_my_commands.await_args.kwargs["scope"].chat_id == 9101
