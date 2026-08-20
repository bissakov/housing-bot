import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from bot.config import BOT_TOKEN, DEV_MODE, REDIS_URL, get_settings
from bot.services.llm import init_llm
from bot.database import async_session, init_db, close_db
from bot.middlewares import DbSessionMiddleware
from bot.handlers import common, resident, worker, dispatcher
from bot.services.scheduler import setup_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    init_llm()  # read LLM_* from config/env
    get_settings().validate_runtime()

    await init_db()
    logger.info("DB initialized")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = MemoryStorage()
    if REDIS_URL:
        try:
            from aiogram.fsm.storage.redis import RedisStorage

            storage = RedisStorage.from_url(REDIS_URL)
            logger.info("Using Redis FSM storage")
        except ImportError as exc:
            raise RuntimeError(
                "REDIS_URL is configured but the redis dependency is unavailable"
            ) from exc
    dp = Dispatcher(storage=storage)

    # Middleware: inject DB session
    dp.message.middleware(DbSessionMiddleware(async_session))
    dp.callback_query.middleware(DbSessionMiddleware(async_session))

    # Routers - order matters: common first, then role-specific
    dp.include_router(common.router)
    dp.include_router(resident.router)
    dp.include_router(worker.router)
    dp.include_router(dispatcher.router)

    # DEV-only router: /dev switch (guarded at handler level, but only include when DEV_MODE)
    if DEV_MODE:
        from bot.handlers import dev as dev_handler
        dp.include_router(dev_handler.router)
        logger.warning("DEV_MODE=ON — /dev handler enabled (disable in prod)")

    # Register the command menu shown in Telegram clients.
    # Keep in sync with the actual slash handlers in bot/handlers/*.
    commands = [BotCommand(command="start", description="Регистрация и главное меню")]
    if DEV_MODE:
        commands.append(BotCommand(command="dev", description="Быстро сменить роль (только для разработки и отладки)"))
        commands.append(BotCommand(command="reset", description="Удалить профиль и заново зарегистрироваться (только для разработки и отладки)"))
    try:
        await bot.set_my_commands(commands)
        logger.info("Bot commands registered: %s", [c.command for c in commands])
    except Exception as exc:  # noqa: BLE001 - don't block startup on menu sync
        logger.warning("Failed to set bot commands: %s", exc)

    scheduler = setup_scheduler(bot, async_session)
    logger.info("Scheduler started")

    logger.info("Bot starting polling...")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await dp.storage.close()
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
