import json
import pytest
from bot.services.llm.client import LLMClient, ClassifyResult

@pytest.mark.asyncio
async def test_classify_and_enrich_parses_json():
    c = LLMClient(api_key="sk-test", enabled=True)
    async def fake_chat(messages, temperature=0.2, max_tokens=0, json_mode=False):
        assert json_mode is True
        return json.dumps({"category":"security","confidence":0.92,"reason":"запах газа","urgency":"high","enriched":"Запах газа на кухне, срочно"})
    c._chat = fake_chat  # type: ignore
    res = await c.classify_and_enrich("пахнет газом на кухне")
    assert res.category == "security"
    assert res.confidence == 0.92
    assert res.urgency == "high"
    assert "газ" in res.enriched.lower()

@pytest.mark.asyncio
async def test_classify_fence_stripping():
    c = LLMClient(api_key="sk-test", enabled=True)
    async def fake_chat(messages, temperature=0.2, max_tokens=0, json_mode=False):
        return '```json\n{"category":"plumber","confidence":0.88,"urgency":"high","enriched":"Течёт кран"}\n```'
    c._chat = fake_chat  # type: ignore
    res = await c.classify_and_enrich("течет кран")
    assert res.category == "plumber"

@pytest.mark.asyncio
async def test_polish_returns_text():
    c = LLMClient(api_key="sk-test", enabled=True)
    async def fake_chat(messages, temperature=0.2, max_tokens=0, json_mode=False):
        return "Уважаемые жители! Отключение воды 12.05 с 10:00."
    c._chat = fake_chat  # type: ignore
    res = await c.polish("вода не будет 12.05")
    assert "воды" in res.text.lower() or "Отключение" in res.text

@pytest.mark.asyncio
async def test_triage():
    c = LLMClient(api_key="sk-test", enabled=True)
    async def fake_chat(messages, temperature=0.2, max_tokens=0, json_mode=False):
        return json.dumps({"priority":"high","summary":"Затопление","hint":"Срочно"})
    c._chat = fake_chat  # type: ignore
    tri = await c.triage("течет сильно", "plumber")
    assert tri.priority == "high"

def test_disabled_client():
    c = LLMClient(api_key="", enabled=False)
    assert not c.enabled

@pytest.mark.asyncio
async def test_resident_llm_auto_category_flow(monkeypatch, engine, session):
    """Integration: freeform creates request with llm category when threshold met."""
    import bot.handlers.resident as res_mod
    from bot.services.llm import init_llm
    from bot.models import User, Request
    from sqlalchemy import select
    import bot.config as cfg
    from tests.conftest import make_message
    cfg.LLM_ENABLED = True
    cfg.LLM_AUTO_CATEGORY_THRESHOLD = 0.85
    llm = init_llm(api_key="sk-test", enabled=True)
    async def fake_classify(raw):
        return ClassifyResult(category="plumber", confidence=0.95, enriched="Течь крана на кухне, лужа", urgency="high", reason="течь")
    monkeypatch.setattr(llm, "classify_and_enrich", fake_classify)

    # engine/session is isolated in-memory DB from conftest fixture
    u = User(telegram_id=11111, full_name="Житель", apartment="5", role="resident", is_approved=True)
    session.add(u)
    await session.flush()

    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from bot.states import RequestStates
    storage = MemoryStorage()
    from unittest.mock import AsyncMock, MagicMock
    from aiogram import Bot as AiogramBot
    bot = AsyncMock(spec=AiogramBot)
    bot.id = 123
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    key = StorageKey(bot_id=bot.id, chat_id=11111, user_id=11111)
    state = FSMContext(storage=storage, key=key)

    await state.set_state(RequestStates.waiting_description)
    await state.update_data(category=None, llm_intake=True)

    msg = make_message(text="течет кран на кухне сильно", tg_id=11111)
    await res_mod.input_description(msg, state, session, bot)
    await session.commit()
    r = await session.execute(select(Request).where(Request.resident_id == u.id))
    req = r.scalars().first()
    assert req is not None
    assert req.category == "plumber"
    assert req.urgency == "high"
    assert req.llm_meta is not None
    assert "Течь" in req.description
