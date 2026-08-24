from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import func, select

from bot.handlers.worker import handle_completion_comment
from bot.models import Request, RequestEvent
from bot.states import WorkerCompletionStates
from tests.conftest import create_user, make_message


def completion_state(user_id: int) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=(123456, user_id, user_id),
    )


def network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=MagicMock(), message="connection reset")


async def accepted_request(session, *, worker_telegram_id: int) -> Request:
    resident = await create_user(session, telegram_id=worker_telegram_id + 1)
    worker = await create_user(
        session,
        telegram_id=worker_telegram_id,
        role="worker",
        worker_category="plumber",
        is_on_shift=True,
        full_name="Исполнитель",
    )
    request = Request(
        resident_id=resident.id,
        worker_id=worker.id,
        category="plumber",
        description="Не работает лифт",
        status="accepted",
    )
    session.add(request)
    await session.commit()
    return request


def llm_review():
    llm = MagicMock(enabled=True)
    llm.improve_completion_comment = AsyncMock(
        return_value=SimpleNamespace(
            accepted=True,
            improved="Заявка не выполнена: отсутствует доступ к лифту.",
            suggestion=None,
        )
    )
    return llm


@pytest.mark.asyncio
async def test_completion_ack_retries_transient_network_error(session, fake_bot):
    worker_telegram_id = 4101
    request = await accepted_request(
        session, worker_telegram_id=worker_telegram_id
    )
    state = completion_state(worker_telegram_id)
    await state.set_state(WorkerCompletionStates.waiting_comment)
    await state.set_data(
        {"request_id": request.id, "completion_result": "not_done"}
    )
    message = make_message("Нет доступа к лифту", tg_id=worker_telegram_id)
    message.answer.side_effect = [network_error(), None]
    llm = llm_review()

    with (
        patch("bot.handlers.worker.get_llm", return_value=llm),
        patch("bot.handlers.worker.asyncio.sleep", new=AsyncMock()) as sleep,
        patch("bot.handlers.worker.notify_resident", new=AsyncMock()) as resident_notify,
        patch("bot.handlers.worker.notify_dispatchers", new=AsyncMock()) as dispatcher_notify,
    ):
        await handle_completion_comment(message, session, state, fake_bot)

    assert message.answer.await_count == 2
    sleep.assert_awaited_once_with(0.5)
    assert await state.get_state() is None
    await session.refresh(request)
    assert request.status == "closed"
    resident_notify.assert_awaited_once()
    dispatcher_notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_comment_replays_committed_completion(session, fake_bot):
    worker_telegram_id = 4201
    request = await accepted_request(
        session, worker_telegram_id=worker_telegram_id
    )
    state = completion_state(worker_telegram_id)
    await state.set_state(WorkerCompletionStates.waiting_comment)
    await state.set_data(
        {"request_id": request.id, "completion_result": "not_done"}
    )
    first_message = make_message("Нет доступа к лифту", tg_id=worker_telegram_id)
    first_message.answer.side_effect = [
        network_error(),
        network_error(),
        network_error(),
    ]
    llm = llm_review()

    with (
        patch("bot.handlers.worker.get_llm", return_value=llm),
        patch("bot.handlers.worker.asyncio.sleep", new=AsyncMock()),
        patch("bot.handlers.worker.notify_resident", new=AsyncMock()) as resident_notify,
        patch("bot.handlers.worker.notify_dispatchers", new=AsyncMock()) as dispatcher_notify,
    ):
        with pytest.raises(TelegramNetworkError):
            await handle_completion_comment(first_message, session, state, fake_bot)

        await session.refresh(request)
        assert request.status == "closed"
        assert await state.get_state() == WorkerCompletionStates.waiting_comment.state
        resident_notify.assert_not_awaited()
        dispatcher_notify.assert_not_awaited()

        duplicate = make_message("Нет доступа к лифту", tg_id=worker_telegram_id)
        await handle_completion_comment(duplicate, session, state, fake_bot)

    assert "отмечена как «не выполнена»" in duplicate.answer.await_args.args[0]
    assert await state.get_state() is None
    llm.improve_completion_comment.assert_awaited_once()
    resident_notify.assert_awaited_once()
    dispatcher_notify.assert_awaited_once()

    closed_events = await session.scalar(
        select(func.count())
        .select_from(RequestEvent)
        .where(
            RequestEvent.request_id == request.id,
            RequestEvent.action == "closed",
        )
    )
    assert closed_events == 1
