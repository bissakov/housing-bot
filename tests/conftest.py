import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from aiogram import Bot as AiogramBot
from aiogram.types import User as TgUser, Chat, Message, CallbackQuery
from unittest.mock import AsyncMock, MagicMock, patch, create_autospec
from datetime import datetime

from bot.models import Base, User, Request, Announcement

# ---------- DB fixtures ----------

@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()

@pytest_asyncio.fixture
async def session(engine):
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sm() as s:
        yield s
        await s.rollback()

@pytest.fixture
def fake_bot():
    bot = AsyncMock(spec=AiogramBot)
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    return bot

# ---------- helpers - use MagicMock wrappers instead of mutating frozen Message ----------

def make_tg_user(tg_id=123, username="testuser", first_name="Test"):
    return TgUser(id=tg_id, is_bot=False, first_name=first_name, username=username)

def make_message(text="/start", tg_id=123, username="testuser", first_name="Test"):
    user = make_tg_user(tg_id, username, first_name)
    chat = Chat(id=tg_id, type="private", username=username, first_name=first_name)
    msg = Message(
        message_id=1,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text=text,
    )
    # Wrap in MagicMock that delegates attributes but allows mocking .answer
    wrapper = MagicMock(wraps=msg)
    wrapper.text = text
    wrapper.from_user = user
    wrapper.chat = chat
    wrapper.message_id = 1
    wrapper.date = msg.date
    wrapper.answer = AsyncMock()
    wrapper.edit_text = AsyncMock()
    wrapper.reply = AsyncMock()
    # keep original for debugging
    wrapper._orig = msg
    return wrapper

def make_callback(data: str, tg_id=123, msg_text="orig"):
    user = make_tg_user(tg_id)
    chat = Chat(id=tg_id, type="private")
    msg = Message(message_id=10, date=datetime.now(), chat=chat, from_user=user, text=msg_text)
    wrapper_msg = MagicMock(wraps=msg)
    wrapper_msg.text = msg_text
    wrapper_msg.chat = chat
    wrapper_msg.from_user = user
    wrapper_msg.answer = AsyncMock()
    wrapper_msg.edit_text = AsyncMock()
    cb = MagicMock(spec=CallbackQuery)
    cb.id = "cb1"
    cb.from_user = user
    cb.data = data
    cb.message = wrapper_msg
    cb.answer = AsyncMock()
    return cb

async def create_user(session, telegram_id=1001, role="resident", is_approved=True, worker_category=None, is_on_shift=False, full_name="Test User", apartment="42"):
    u = User(
        telegram_id=telegram_id,
        role=role,
        is_approved=is_approved,
        worker_category=worker_category,
        is_on_shift=is_on_shift,
        full_name=full_name,
        apartment=apartment,
        language="ru",
    )
    session.add(u)
    await session.flush()
    return u
