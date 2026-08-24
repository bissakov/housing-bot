"""Localized Telegram command menus."""

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

from bot.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, normalize_language, t


def command_menu(language: str | None, *, include_dev: bool) -> list[BotCommand]:
    """Build a command menu in exactly one supported language."""
    language = normalize_language(language)
    commands = [
        BotCommand(command="start", description=t("command_start", language)),
        BotCommand(command="language", description=t("command_language", language)),
    ]
    if include_dev:
        commands.extend(
            [
                BotCommand(command="dev", description=t("command_dev", language)),
                BotCommand(command="reset", description=t("command_reset", language)),
            ]
        )
    return commands


async def register_global_command_menus(bot: Bot, *, include_dev: bool) -> None:
    """Register fallbacks for users who have not selected a bot language yet."""
    await bot.set_my_commands(
        command_menu(DEFAULT_LANGUAGE, include_dev=include_dev)
    )
    for language in sorted(SUPPORTED_LANGUAGES):
        await bot.set_my_commands(
            command_menu(language, include_dev=include_dev),
            language_code=language,
        )


async def set_chat_command_menu(
    bot: Bot,
    chat_id: int,
    language: str | None,
    *,
    include_dev: bool,
) -> None:
    """Apply the user's saved bot language to their private-chat menu."""
    await bot.set_my_commands(
        command_menu(language, include_dev=include_dev),
        scope=BotCommandScopeChat(chat_id=chat_id),
    )
