from datetime import datetime, timezone

from sqlalchemy import select
from unittest.mock import AsyncMock

from bot.auth import is_dispatcher
from bot.models import RequestAttachment, User
from bot.services.request_routing import next_cleaning_dispatch
from bot.services.requests import (
    approve_request,
    assign_request,
    claim_request,
    create_request,
    reject_request,
)


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_administrator_is_the_internal_chairman_super_role():
    chairman = User(role="administrator", is_approved=True, telegram_id=1)
    assert is_dispatcher(chairman)


def test_cleaning_schedule_releases_at_next_opening():
    # Asia/Almaty is UTC+5: Monday 08:00-13:00 is 03:00-08:00 UTC.
    assert next_cleaning_dispatch(_utc(2026, 8, 24, 4)) is None
    assert next_cleaning_dispatch(_utc(2026, 8, 24, 2)) == _utc(2026, 8, 24, 3)
    assert next_cleaning_dispatch(_utc(2026, 8, 29, 8)) == _utc(2026, 8, 31, 3)
    assert next_cleaning_dispatch(_utc(2026, 8, 30, 5)) == _utc(2026, 8, 31, 3)


async def test_kazakhdomofon_is_generic_work_after_chairman_approval(session):
    resident = User(telegram_id=10, role="resident", is_approved=True)
    worker = User(
        telegram_id=11,
        role="worker",
        worker_category="kazakhdomofon",
        is_approved=True,
        is_on_shift=True,
    )
    chairman = User(telegram_id=12, role="administrator", is_approved=True)
    session.add_all([resident, worker, chairman])
    await session.flush()

    request = await create_request(
        session,
        resident.id,
        "kazakhdomofon",
        "Добавление Face ID",
        attachments=[{
            "file_id": "telegram-file",
            "file_unique_id": "unique-file",
            "media_type": "photo",
        }],
    )
    assert request.approval_status == "pending"
    assert not (await claim_request(session, request.id, worker))[0]

    assert (await approve_request(session, request.id, chairman))[0]
    await session.refresh(request)
    assert request.approval_status == "approved"
    assert (await claim_request(session, request.id, worker))[0]
    attachment = (
        await session.execute(
            select(RequestAttachment).where(
                RequestAttachment.request_id == request.id
            )
        )
    ).scalar_one()
    assert attachment.media_type == "photo"


async def test_chairman_rejection_requires_comment(session):
    resident = User(telegram_id=20, role="resident", is_approved=True)
    chairman = User(telegram_id=21, role="administrator", is_approved=True)
    session.add_all([resident, chairman])
    await session.flush()
    request = await create_request(
        session, resident.id, "kazakhdomofon", "Изготовление магнитов"
    )

    assert not (await reject_request(session, request.id, chairman, ""))[0]
    assert (await reject_request(
        session, request.id, chairman, "Не подтверждены данные"
    ))[0]
    await session.refresh(request)
    assert request.status == "closed"
    assert request.approval_status == "rejected"
    assert request.completion_result == "not_done"


async def test_cleaning_cannot_be_manually_assigned(session):
    resident = User(telegram_id=30, role="resident", is_approved=True)
    cleaner = User(
        telegram_id=31,
        role="worker",
        worker_category="cleaning",
        is_approved=True,
        is_on_shift=True,
    )
    chairman = User(telegram_id=32, role="administrator", is_approved=True)
    session.add_all([resident, cleaner, chairman])
    await session.flush()
    request = await create_request(
        session,
        resident.id,
        "cleaning",
        "Грязный пол возле первого подъезда",
    )

    success, message = await assign_request(
        session, request.id, cleaner.id, actor=chairman
    )
    assert not success
    assert "только клининг" in message


async def test_service_area_is_stored_on_regular_request(session):
    resident = User(telegram_id=40, role="resident", is_approved=True)
    session.add(resident)
    await session.flush()
    request = await create_request(
        session,
        resident.id,
        "plumber",
        "Течёт кран на кухне",
        service_area="apartment",
    )
    assert request.service_area == "apartment"


async def test_participant_buttons_do_not_expose_internal_role_codes(session):
    from bot.handlers.chairman import list_participants
    from tests.conftest import make_message

    chairman = User(
        telegram_id=50, role="administrator", is_approved=True, language="ru"
    )
    dispatcher = User(
        telegram_id=51, role="dispatcher", is_approved=True,
        full_name="Демо Диспетчер",
    )
    resident = User(
        telegram_id=52, role="resident", is_approved=True,
        full_name="Алексей Новиков",
    )
    session.add_all([chairman, dispatcher, resident])
    await session.commit()
    message = make_message("👥 Участники", tg_id=50)
    message.answer = AsyncMock()

    await list_participants(message, session)

    keyboard = message.answer.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert any("Диспетчер" in label for label in labels)
    assert any("Житель" in label for label in labels)
    assert all("dispatcher" not in label and "resident" not in label for label in labels)


async def test_pending_user_cards_do_not_expose_internal_codes(session):
    from bot.handlers.dispatcher import build_pending_detail, build_pending_list

    worker = User(
        telegram_id=60,
        role="worker",
        worker_category="plumber",
        is_approved=False,
        full_name="Сантехник",
        language="ru",
    )
    session.add(worker)
    await session.flush()

    list_text, _ = await build_pending_list(session, 0, language="ru")
    detail_text, _ = await build_pending_detail(
        session, worker.id, 0, language="ru"
    )
    visible_text = list_text + detail_text
    assert "Исполнитель" in visible_text
    assert "Сантехник" in visible_text
    assert "worker" not in visible_text
    assert "plumber" not in visible_text
    assert "False" not in visible_text
