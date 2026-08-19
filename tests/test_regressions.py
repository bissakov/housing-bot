import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from bot.models import Request, User
from bot.services.llm.client import LLMClient
from bot.services.requests import assign_request, close_request, create_request


@pytest.mark.asyncio
async def test_create_request_rejects_unknown_category(session):
    resident = User(telegram_id=801, role="resident", is_approved=True)
    session.add(resident)
    await session.flush()

    with pytest.raises(ValueError, match="category"):
        await create_request(session, resident.id, "gas", "Запах газа")


@pytest.mark.asyncio
async def test_closed_request_cannot_be_reassigned(session):
    resident = User(telegram_id=802, role="resident", is_approved=True)
    worker = User(
        telegram_id=803,
        role="worker",
        is_approved=True,
        is_on_shift=True,
        worker_category="plumber",
    )
    session.add_all([resident, worker])
    await session.flush()
    request = Request(
        resident_id=resident.id,
        worker_id=worker.id,
        category="plumber",
        description="Течет кран",
        status="closed",
    )
    session.add(request)
    await session.flush()

    ok, _ = await assign_request(session, request.id, worker.id)
    assert not ok
    await session.refresh(request)
    assert request.status == "closed"


@pytest.mark.asyncio
async def test_worker_cannot_close_another_workers_request(session):
    resident = User(telegram_id=804, role="resident", is_approved=True)
    owner = User(telegram_id=805, role="worker", is_approved=True, worker_category="plumber")
    attacker = User(telegram_id=806, role="worker", is_approved=True, worker_category="plumber")
    session.add_all([resident, owner, attacker])
    await session.flush()
    request = Request(
        resident_id=resident.id,
        worker_id=owner.id,
        category="plumber",
        description="Течет кран",
        status="accepted",
    )
    session.add(request)
    await session.flush()

    ok, _ = await close_request(session, request.id, attacker)
    assert not ok
    await session.refresh(request)
    assert request.status == "accepted"


@pytest.mark.asyncio
async def test_unsupported_llm_category_forces_manual_selection(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test")
    client = LLMClient()

    async def fake_chat(_messages, **_kwargs):
        return json.dumps(
            {
                "category": "gas",
                "confidence": 0.99,
                "reason": "запах газа",
                "urgency": "high",
                "enriched": "Сильный запах газа на кухне",
            }
        )

    monkeypatch.setattr(client, "_chat", fake_chat)
    result = await client.classify_and_enrich("Пахнет газом")
    assert result is not None
    assert result.category == ""
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_notify_dispatchers_skips_seeded_demo_accounts(session, fake_bot):
    """Seeded demo dispatchers have no real chat — don't waste an API call on them."""
    from bot.constants import SEED_TG_START
    from bot.services.notify import notify_dispatchers

    session.add_all([
        User(telegram_id=555, role="dispatcher", is_approved=True),
        User(telegram_id=SEED_TG_START, role="dispatcher", is_approved=True),
        User(telegram_id=SEED_TG_START + 3, role="dispatcher", is_approved=True),
    ])
    await session.flush()

    with patch("bot.services.notify.ADMIN_IDS", []):
        report = await notify_dispatchers(fake_bot, session, "hi", parse_mode="HTML")

    assert report.delivered == 1
    assert report.failed == 0
    assert [c.args[0] for c in fake_bot.send_message.await_args_list] == [555]
    assert fake_bot.send_message.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_notify_dispatchers_chat_not_found_is_not_an_error(session, fake_bot, caplog):
    """'chat not found' means the dispatcher never opened the bot: info, no traceback."""
    from aiogram.exceptions import TelegramBadRequest
    from bot.services.notify import notify_dispatchers

    session.add(User(telegram_id=556, role="dispatcher", is_approved=True))
    await session.flush()
    fake_bot.send_message.side_effect = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: chat not found"
    )

    with patch("bot.services.notify.ADMIN_IDS", []), caplog.at_level(logging.DEBUG):
        report = await notify_dispatchers(fake_bot, session, "hi")

    assert report.delivered == 0 and report.failed == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
