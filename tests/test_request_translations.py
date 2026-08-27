import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from bot.models import RequestTranslation, User
from bot.services.request_translations import (
    format_description_html,
    localize_request_description,
    localize_request_descriptions,
)
from bot.services.notify import notify_workers
from bot.services.requests import create_request


@pytest.mark.asyncio
async def test_translation_is_saved_and_reused(session):
    resident = User(
        telegram_id=501,
        role="resident",
        is_approved=True,
        language="ru",
    )
    session.add(resident)
    await session.flush()
    request = await create_request(
        session,
        resident_id=resident.id,
        category="plumber",
        description="На кухне течёт труба",
    )
    llm = MagicMock(enabled=True)
    llm.translate_request = AsyncMock(return_value="Ас үйде құбыр ағып жатыр")

    with patch("bot.services.request_translations.get_llm", return_value=llm):
        first = await localize_request_description(session, request, "kk")
        await session.commit()
        second = await localize_request_description(session, request, "kk")

    assert first == second
    assert first.original == "На кухне течёт труба"
    assert first.translation == "Ас үйде құбыр ағып жатыр"
    llm.translate_request.assert_awaited_once_with(
        "На кухне течёт труба", "kk"
    )
    count = await session.scalar(select(func.count()).select_from(RequestTranslation))
    assert count == 1


@pytest.mark.asyncio
async def test_each_target_language_has_its_own_cache_entry(session):
    resident = User(
        telegram_id=502,
        role="resident",
        is_approved=True,
        language="ru",
    )
    session.add(resident)
    await session.flush()
    request = await create_request(
        session,
        resident_id=resident.id,
        category="electrician",
        description="Light is out",
    )
    llm = MagicMock(enabled=True)
    llm.translate_request = AsyncMock(side_effect=["Жарық жоқ", "Нет света"])

    with patch("bot.services.request_translations.get_llm", return_value=llm):
        kazakh = await localize_request_description(session, request, "kk")
        russian = await localize_request_description(session, request, "ru")

    assert kazakh.translation == "Жарық жоқ"
    assert russian.translation == "Нет света"
    assert llm.translate_request.await_count == 2


@pytest.mark.asyncio
async def test_immediate_cache_write_releases_read_transaction(session):
    resident = User(
        telegram_id=506,
        role="resident",
        is_approved=True,
        language="ru",
    )
    session.add(resident)
    await session.flush()
    request = await create_request(
        session,
        resident_id=resident.id,
        category="plumber",
        description="Течёт труба",
    )
    await session.commit()
    llm = MagicMock(enabled=True)
    llm.translate_request = AsyncMock(return_value="Құбыр ағып жатыр")

    commit = AsyncMock(wraps=session.commit)
    with (
        patch("bot.services.request_translations.get_llm", return_value=llm),
        patch.object(session, "commit", new=commit),
    ):
        result = await localize_request_description(
            session, request, "kk", commit_immediately=True
        )

    assert result.translation == "Құбыр ағып жатыр"
    assert commit.await_count == 2


@pytest.mark.asyncio
async def test_locked_cache_write_still_returns_translation(session):
    resident = User(
        telegram_id=507,
        role="resident",
        is_approved=True,
        language="ru",
    )
    session.add(resident)
    await session.flush()
    request = await create_request(
        session,
        resident_id=resident.id,
        category="plumber",
        description="Течёт труба",
    )
    llm = MagicMock(enabled=True)
    llm.translate_request = AsyncMock(return_value="Құбыр ағып жатыр")
    original_flush = session.flush

    async def locked_translation_flush(*args, **kwargs):
        if any(isinstance(row, RequestTranslation) for row in session.new):
            raise OperationalError(
                "INSERT INTO request_translations",
                {},
                Exception("database is locked"),
            )
        return await original_flush(*args, **kwargs)

    with (
        patch("bot.services.request_translations.get_llm", return_value=llm),
        patch.object(session, "flush", new=locked_translation_flush),
    ):
        result = await localize_request_description(session, request, "kk")

    assert result.original == "Течёт труба"
    assert result.translation == "Құбыр ағып жатыр"


@pytest.mark.asyncio
async def test_request_batch_translates_concurrently(session):
    resident = User(
        telegram_id=508,
        role="resident",
        is_approved=True,
        language="ru",
    )
    session.add(resident)
    await session.flush()
    requests = [
        await create_request(
            session,
            resident_id=resident.id,
            category="plumber",
            description=description,
        )
        for description in ("Течёт труба", "Сломан кран")
    ]
    active = 0
    peak_active = 0

    async def translate(text, language):
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0)
        active -= 1
        return f"{text} ({language})"

    llm = MagicMock(enabled=True)
    llm.translate_request = AsyncMock(side_effect=translate)
    with patch("bot.services.request_translations.get_llm", return_value=llm):
        localized = await localize_request_descriptions(
            session, requests, "kk", commit_immediately=True
        )

    assert peak_active == 2
    assert set(localized) == {request.id for request in requests}
    assert llm.translate_request.await_count == 2


def test_render_shows_translation_and_original():
    from bot.services.request_translations import LocalizedRequestDescription

    rendered = format_description_html(
        LocalizedRequestDescription(
            original="Течёт <кран>",
            translation="Краннан су ағып тұр",
        ),
        "kk",
    )

    assert "ЖИ аудармасы" in rendered
    assert "Краннан су ағып тұр" in rendered
    assert "Түпнұсқа" in rendered
    assert "Течёт &lt;кран&gt;" in rendered


@pytest.mark.asyncio
async def test_two_workers_share_one_saved_translation(session, fake_bot):
    resident = User(
        telegram_id=503,
        role="resident",
        is_approved=True,
        language="ru",
        full_name="Resident",
        apartment="42",
    )
    workers = [
        User(
            telegram_id=telegram_id,
            role="worker",
            worker_category="plumber",
            is_approved=True,
            language="kk",
        )
        for telegram_id in (504, 505)
    ]
    session.add_all([resident, *workers])
    await session.flush()
    request = await create_request(
        session,
        resident_id=resident.id,
        category="plumber",
        description="На кухне течёт труба",
    )
    llm = MagicMock(enabled=True)
    llm.translate_request = AsyncMock(return_value="Ас үйде құбыр ағып жатыр")

    with patch("bot.services.request_translations.get_llm", return_value=llm):
        report = await notify_workers(
            fake_bot,
            session,
            "plumber",
            "",
            force_all=True,
            message_key="new_request_notification",
            message_values={
                "id": request.id,
                "category": request.category,
                "address": "42",
                "resident": "Resident",
            },
            request=request,
        )

    assert report.delivered == 2
    llm.translate_request.assert_awaited_once()
    for call in fake_bot.send_message.await_args_list:
        message = call.args[1]
        assert "Ас үйде құбыр ағып жатыр" in message
        assert "На кухне течёт труба" in message
