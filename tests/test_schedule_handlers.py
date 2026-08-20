import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers.dispatcher import schedule_hours_start
from bot.models import User
from tests.conftest import make_callback


@pytest.mark.asyncio
async def test_schedule_hours_prompt_uses_dispatcher_language(session):
    dispatcher = User(
        telegram_id=9101,
        role="dispatcher",
        is_approved=True,
        language="ru",
        full_name="Dispatcher",
    )
    session.add(dispatcher)
    await session.flush()
    callback = make_callback("schedule_hours:123", tg_id=dispatcher.telegram_id)
    state = FSMContext(storage=MemoryStorage(), key=(1, 9101, 9101))

    await schedule_hours_start(callback, state, session)

    kwargs = callback.message.answer.await_args.kwargs
    assert kwargs["reply_markup"].keyboard[0][0].text == "❌ Отмена"
    assert "Введите дни и часы" in callback.message.answer.await_args.args[0]
