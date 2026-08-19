import asyncio
import logging
from aiogram import Bot, Dispatcher
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
