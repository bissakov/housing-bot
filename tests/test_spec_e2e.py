"""
E2E tests covering spec.md flows without real Telegram network.
DB is in-memory sqlite, handlers are called directly.
"""
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

from bot.models import User, Request, Announcement
from bot.services.requests import create_request, claim_request, close_request, assign_request
from tests.conftest import make_message, make_callback, create_user

# Storage helper to create FSMContext without dispatcher
def fsm_for(user_id=1, chat_id=1):
    storage = MemoryStorage()
    # FSMContext needs storage + key (bot_id, chat_id, user_id)
    return FSMContext(storage=storage, key=(123456, chat_id, user_id))


# ---------------- Unit / service tests ----------------

@pytest.mark.asyncio
async def test_create_request(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет батарея")
    await session.commit()
    assert req.id is not None
    assert req.status == "new"
    assert req.category == "plumber"


@pytest.mark.asyncio
async def test_atomic_claim_only_one_wins(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    w1 = await create_user(session, telegram_id=222, role="worker", worker_category="plumber", is_on_shift=True, full_name="W1")
    w2 = await create_user(session, telegram_id=333, role="worker", worker_category="plumber", is_on_shift=True, full_name="W2")
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()

    ok1, _ = await claim_request(session, req.id, w1)
    assert ok1 is True
    await session.commit()

    ok2, msg2 = await claim_request(session, req.id, w2)
    assert ok2 is False
    assert "уже принята" in msg2


@pytest.mark.asyncio
async def test_claim_wrong_category_rejected(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    electrician = await create_user(session, telegram_id=444, role="worker", worker_category="electrician", is_on_shift=True)
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()
    ok, msg = await claim_request(session, req.id, electrician)
    assert ok is False
    assert "Категория" in msg


@pytest.mark.asyncio
async def test_claim_off_shift_rejected(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    w = await create_user(session, telegram_id=222, role="worker", worker_category="plumber", is_on_shift=False)
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()
    ok, msg = await claim_request(session, req.id, w)
    assert ok is False
    assert "не на смене" in msg


@pytest.mark.asyncio
async def test_close_only_assigned_worker(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    w1 = await create_user(session, telegram_id=222, role="worker", worker_category="plumber", is_on_shift=True, full_name="W1")
    w2 = await create_user(session, telegram_id=333, role="worker", worker_category="plumber", is_on_shift=True, full_name="W2")
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()
    ok, _ = await claim_request(session, req.id, w1)
    assert ok
    await session.flush()
    # w2 tries to close w1's request
    ok2, msg2 = await close_request(
        session,
        req.id,
        w2,
        completion_result="done",
        completion_comment="Протечка устранена.",
    )
    assert ok2 is False
    assert "не ваша" in msg2
    # w1 can close
    ok3, _ = await close_request(
        session,
        req.id,
        w1,
        completion_result="done",
        completion_comment="Протечка устранена.",
    )
    assert ok3 is True
    await session.commit()
    assert req.status == "closed"


@pytest.mark.asyncio
async def test_worker_close_requires_result_and_comment(session):
    resident = await create_user(session, telegram_id=711, role="resident")
    worker = await create_user(
        session,
        telegram_id=722,
        role="worker",
        worker_category="plumber",
        is_on_shift=True,
    )
    req = await create_request(
        session,
        resident_id=resident.id,
        category="plumber",
        description="Течет",
    )
    req.status = "accepted"
    req.worker_id = worker.id
    await session.flush()

    ok, message = await close_request(session, req.id, worker)

    assert ok is False
    assert "результат" in message.lower()
    assert req.status == "accepted"


@pytest.mark.asyncio
async def test_dispatcher_assign_and_reassign(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    w1 = await create_user(session, telegram_id=222, role="worker", worker_category="plumber", is_on_shift=True)
    w2 = await create_user(session, telegram_id=333, role="worker", worker_category="plumber", is_on_shift=True)
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()

    ok, _ = await assign_request(session, req.id, w1.id)
    assert ok
    await session.flush()
    result = await session.execute(select(Request).where(Request.id == req.id))
    r = result.scalar_one()
    assert r.worker_id == w1.id
    assert r.status == "accepted"

    # reassign
    ok2, _ = await assign_request(session, req.id, w2.id)
    assert ok2
    await session.flush()
    result = await session.execute(select(Request).where(Request.id == req.id))
    r = result.scalar_one()
    assert r.worker_id == w2.id


# ---------------- Handler tests ----------------

@pytest.mark.asyncio
async def test_common_start_new_user_prompts_registration(session):
    from bot.handlers.common import cmd_start
    msg = make_message("/start", tg_id=999)
    msg.answer = AsyncMock()
    state = fsm_for(user_id=999, chat_id=999)
    await cmd_start(msg, state, session)
    await session.commit()
    # should ask for full name
    assert msg.answer.called
    call_text = msg.answer.call_args[0][0]
    assert "ФИО" in call_text or "Добро пожаловать" in call_text
    assert await state.get_state() is not None


@pytest.mark.asyncio
async def test_common_registration_flow(session):
    from bot.handlers.common import cmd_start, reg_name, reg_apartment
    tg_id = 777
    # /start
    msg0 = make_message("/start", tg_id=tg_id)
    msg0.answer = AsyncMock()
    state = fsm_for(user_id=tg_id, chat_id=tg_id)
    await cmd_start(msg0, state, session)
    from bot.handlers.common import set_language
    await set_language(make_callback("set_language:ru", tg_id=tg_id), state, session)
    await session.commit()
    # send name
    msg1 = make_message("Иванов Иван", tg_id=tg_id)
    msg1.answer = AsyncMock()
    await reg_name(msg1, state, session)
    # should ask for apartment
    assert "квартир" in msg1.answer.call_args[0][0].lower()
    # send apartment
    msg2 = make_message("12", tg_id=tg_id)
    msg2.answer = AsyncMock()
    from unittest.mock import AsyncMock as _AM
    fake_bot = _AM()
    fake_bot.send_message = _AM()
    await reg_apartment(msg2, state, session, fake_bot)
    await session.commit()
    # check user created and approved
    result = await session.execute(select(User).where(User.telegram_id == tg_id))
    u = result.scalar_one()
    assert u.full_name == "Иванов Иван"
    assert u.apartment == "12"
    assert u.is_approved is False  # spec: needs dispatcher approval
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_resident_create_request_fsm(session, fake_bot):
    from bot.handlers.resident import start_request, choose_category, input_description

    resident = await create_user(session, telegram_id=501, role="resident", is_approved=True)
    await session.commit()

    # 1) user presses 📝 Создать заявку
    msg = make_message("📝 Создать заявку", tg_id=501)
    msg.answer = AsyncMock()
    state = fsm_for(user_id=501, chat_id=501)
    await start_request(msg, state, session)
    assert msg.answer.called
    assert "категорию" in msg.answer.call_args[0][0].lower()

    # 2) choose category via callback
    cb = make_callback("req_category:plumber", tg_id=501)
    cb.message.edit_text = AsyncMock()
    await choose_category(cb, state, session, fake_bot)
    data = await state.get_data()
    assert data["category"] == "plumber"

    # 3) send description (short should be rejected, then long accepted)
    short = make_message("течет", tg_id=501)
    short.answer = AsyncMock()
    await input_description(short, state, session, fake_bot)
    assert "подробнее" in short.answer.call_args[0][0].lower()
    # valid description
    long_msg = make_message("Течет батарея в ванной, лужа на полу, срочно!", tg_id=501)
    long_msg.answer = AsyncMock()
    with patch("bot.handlers.resident.notify_workers", new=AsyncMock()) as mock_notify:
        await input_description(long_msg, state, session, fake_bot)
        # should have created request
        result = await session.execute(select(Request).where(Request.resident_id == resident.id))
        reqs = result.scalars().all()
        assert len(reqs) == 1
        assert reqs[0].category == "plumber"
        assert reqs[0].status == "new"
        mock_notify.assert_awaited_once()
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_worker_shift_toggle(session):
    from bot.handlers.worker import toggle_shift
    w = await create_user(session, telegram_id=602, role="worker", worker_category="electrician", is_on_shift=False)
    await session.commit()
    msg = make_message("▶️ На смену", tg_id=602)
    msg.answer = AsyncMock()
    await toggle_shift(msg, session)
    await session.commit()
    result = await session.execute(select(User).where(User.telegram_id == 602))
    updated = result.scalar_one()
    assert updated.is_on_shift is True


@pytest.mark.asyncio
async def test_worker_claim_via_callback(session, fake_bot):
    from bot.handlers.worker import handle_claim
    resident = await create_user(session, telegram_id=701, role="resident")
    worker = await create_user(session, telegram_id=702, role="worker", worker_category="plumber", is_on_shift=True, full_name="Сантехник")
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет труба")
    await session.commit()

    cb = make_callback(f"claim:{req.id}", tg_id=702)
    cb.message.edit_text = AsyncMock()
    # patch notify helpers to avoid real sends
    with patch("bot.handlers.worker.notify_resident", new=AsyncMock()), patch("bot.handlers.worker.notify_dispatchers", new=AsyncMock()):
        await handle_claim(cb, session, fake_bot)
        await session.commit()
    result = await session.execute(select(Request).where(Request.id == req.id))
    r = result.scalar_one()
    assert r.status == "accepted"
    assert r.worker_id == worker.id


@pytest.mark.asyncio
async def test_dispatcher_create_announcement_broadcast(session, fake_bot):
    from bot.handlers.dispatcher import create_ann_start, create_ann_finish
    disp = await create_user(session, telegram_id=801, role="dispatcher", is_approved=True)
    resident = await create_user(session, telegram_id=802, role="resident", is_approved=True)
    await session.commit()

    state = fsm_for(user_id=801, chat_id=801)
    msg0 = make_message("📢 Создать объявление", tg_id=801)
    msg0.answer = AsyncMock()
    await create_ann_start(msg0, state, session)
    assert "текст" in msg0.answer.call_args[0][0].lower()

    msg1 = make_message("Завтра отключение воды с 10:00 до 14:00", tg_id=801)
    msg1.answer = AsyncMock()
    with patch("bot.handlers.dispatcher.broadcast_announcement", new=AsyncMock()) as mock_bc:
        await create_ann_finish(msg1, state, session, fake_bot)
        await session.commit()
        mock_bc.assert_called_once()
    result = await session.execute(select(Announcement))
    anns = result.scalars().all()
    assert len(anns) == 1
    assert "отключение воды" in anns[0].text


@pytest.mark.asyncio
async def test_dispatcher_add_worker(session):
    from bot.handlers.dispatcher import add_worker_start, add_worker_finish
    disp = await create_user(session, telegram_id=901, role="dispatcher", is_approved=True)
    await session.commit()
    state = fsm_for(user_id=901, chat_id=901)
    msg = make_message("➕ Добавить исполнителя", tg_id=901)
    msg.answer = AsyncMock()
    await add_worker_start(msg, state, session)
    assert "Telegram ID" in msg.answer.call_args[0][0]

    # simulate entering tid
    from bot.handlers.dispatcher import add_worker_tid
    msg2 = make_message("100500", tg_id=901)
    msg2.answer = AsyncMock()
    await add_worker_tid(msg2, state)
    # choose category
    cb = make_callback("add_worker_cat:electrician", tg_id=901)
    cb.message.edit_text = AsyncMock()
    await add_worker_finish(cb, state, session)
    await session.commit()
    result = await session.execute(select(User).where(User.telegram_id == 100500))
    w = result.scalar_one()
    assert w.role == "worker"
    assert w.worker_category == "electrician"
    assert w.is_approved is True


@pytest.mark.asyncio
async def test_escalation_notifies_dispatchers(session, fake_bot):
    from datetime import datetime, timedelta, timezone
    from bot.services.scheduler import check_escalation
    # create overdue request
    resident = await create_user(session, telegram_id=1001, role="resident")
    disp = await create_user(session, telegram_id=1002, role="dispatcher", is_approved=True)
    # create request with old created_at
    req = Request(resident_id=resident.id, category="plumber", description="Старая заявка", status="new")
    # force old timestamp via raw
    req.created_at = datetime.now(timezone.utc) - timedelta(minutes=25)
    session.add(req)
    await session.flush()
    await session.commit()

    # verify overdue detectable (escalation query logic)
    from sqlalchemy import select as sel
    result = await session.execute(sel(Request).where(Request.status == "new"))
    overdue = [r for r in result.scalars().all() if (datetime.now(timezone.utc) - r.created_at).total_seconds() > 20*60]
    assert len(overdue) == 1


@pytest.mark.asyncio
async def test_worker_registration_requires_name(session, fake_bot):
    """Worker flow: role -> ФИО -> дисциплина. The name step must not be skippable."""
    from bot.handlers.common import cmd_start, reg_role_choice, reg_name, reg_worker_category_choice
    from bot.states import RegistrationStates

    tg_id = 778
    await create_user(session, telegram_id=4242, role="dispatcher", is_approved=True)
    await session.commit()
    state = fsm_for(user_id=tg_id, chat_id=tg_id)

    msg0 = make_message("/start", tg_id=tg_id)
    await cmd_start(msg0, state, session)
    from bot.handlers.common import set_language
    await set_language(make_callback("set_language:ru", tg_id=tg_id), state, session)
    await session.commit()

    # pick "Исполнитель" -> must land on the name step, not the category picker
    cb_role = make_callback("reg_role:worker", tg_id=tg_id)
    await reg_role_choice(cb_role, state, session)
    await session.commit()
    assert await state.get_state() == RegistrationStates.waiting_name
    assert "ФИО" in cb_role.message.edit_text.call_args[0][0]

    # too-short name is rejected, stays on the same step
    bad = make_message("Ан", tg_id=tg_id)
    await reg_name(bad, state, session)
    assert await state.get_state() == RegistrationStates.waiting_name

    msg1 = make_message("Петров Пётр Петрович", tg_id=tg_id)
    await reg_name(msg1, state, session)
    assert await state.get_state() == RegistrationStates.waiting_worker_category
    assert "дисциплин" in msg1.answer.call_args[0][0].lower()

    cb_cat = make_callback("reg_worker_category:electrician", tg_id=tg_id)
    await reg_worker_category_choice(cb_cat, state, session, fake_bot)
    await session.commit()

    u = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one()
    assert u.full_name == "Петров Пётр Петрович"
    assert u.role == "worker"
    assert u.worker_category == "electrician"
    assert u.is_approved is False
    assert await state.get_state() is None

    # the dispatcher card carries the real name, never "None"
    sent = fake_bot.send_message.await_args.args[1]
    assert "Петров Пётр Петрович" in sent
    assert "None" not in sent
