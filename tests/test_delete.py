import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from bot.models import Request, Announcement
from bot.services.requests import delete_request, delete_announcement
from tests.conftest import create_user, make_callback, make_message

@pytest.mark.asyncio
async def test_resident_can_delete_own_new(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    from bot.services.requests import create_request
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()
    ok, _ = await delete_request(session, req.id, resident)
    assert ok
    result = await session.execute(select(Request).where(Request.id == req.id))
    assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_resident_cannot_delete_accepted(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    worker = await create_user(session, telegram_id=222, role="worker", worker_category="plumber", is_on_shift=True)
    from bot.services.requests import create_request, claim_request
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()
    await claim_request(session, req.id, worker)
    await session.flush()
    ok, msg = await delete_request(session, req.id, resident)
    assert not ok
    assert "только новую" in msg.lower()

@pytest.mark.asyncio
async def test_dispatcher_can_delete_any(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    disp = await create_user(session, telegram_id=222, role="dispatcher", is_approved=True)
    worker = await create_user(session, telegram_id=333, role="worker", worker_category="plumber", is_on_shift=True)
    from bot.services.requests import create_request, claim_request
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет")
    await session.flush()
    await claim_request(session, req.id, worker)
    await session.flush()
    ok, _ = await delete_request(session, req.id, disp)
    assert ok

@pytest.mark.asyncio
async def test_dispatcher_can_delete_announcement(session):
    disp = await create_user(session, telegram_id=111, role="dispatcher", is_approved=True)
    from bot.services.requests import create_announcement
    ann = await create_announcement(session, author_id=disp.id, text="Вода отключена")
    await session.flush()
    ok, _ = await delete_announcement(session, ann.id, disp)
    assert ok
    result = await session.execute(select(Announcement).where(Announcement.id == ann.id))
    assert result.scalar_one_or_none() is None

@pytest.mark.asyncio
async def test_resident_cannot_delete_announcement(session):
    resident = await create_user(session, telegram_id=111, role="resident")
    disp = await create_user(session, telegram_id=222, role="dispatcher", is_approved=True)
    from bot.services.requests import create_announcement
    ann = await create_announcement(session, author_id=disp.id, text="Вода отключена")
    await session.flush()
    ok, msg = await delete_announcement(session, ann.id, resident)
    assert not ok
    assert "диспетчер" in msg.lower()

@pytest.mark.asyncio
async def test_delete_via_callback_confirm_flow(session, fake_bot):
    # simulate user pressing 🗑️ then ✅ Да, удалить
    from bot.handlers.common import confirm_delete_req, do_delete_req
    resident = await create_user(session, telegram_id=501, role="resident")
    from bot.services.requests import create_request
    req = await create_request(session, resident_id=resident.id, category="plumber", description="Течет батарея")
    await session.commit()

    cb1 = make_callback(f"delete_req:{req.id}", tg_id=501)
    cb1.message.edit_reply_markup = AsyncMock()
    await confirm_delete_req(cb1, session)
    assert cb1.message.edit_reply_markup.called

    cb2 = make_callback(f"confirm_delete_req:{req.id}", tg_id=501)
    cb2.message.edit_text = AsyncMock()
    await do_delete_req(cb2, session)
    await session.commit()
    result = await session.execute(select(Request).where(Request.id == req.id))
    assert result.scalar_one_or_none() is None
    assert "удалена" in cb2.message.edit_text.call_args[0][0].lower()
