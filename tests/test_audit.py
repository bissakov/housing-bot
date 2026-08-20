import pytest
from sqlalchemy import select

from bot.models import Request, RequestEvent, User
from bot.services.requests import (
    assign_request,
    claim_request,
    close_request,
    create_request,
    delete_request,
)


@pytest.mark.asyncio
async def test_request_lifecycle_writes_audit_events(session):
    resident = User(telegram_id=10001, role="resident", is_approved=True)
    worker = User(
        telegram_id=10002,
        role="worker",
        is_approved=True,
        is_on_shift=True,
        worker_category="plumber",
    )
    session.add_all([resident, worker])
    await session.flush()

    request = await create_request(
        session, resident.id, "plumber", "Течет кран"
    )
    ok, _ = await claim_request(session, request.id, worker)
    assert ok
    ok, _ = await close_request(
        session,
        request.id,
        worker,
        completion_result="done",
        completion_comment="Кран отремонтирован, протечка устранена.",
    )
    assert ok

    events = (
        await session.execute(
            select(RequestEvent)
            .where(RequestEvent.request_id == request.id)
            .order_by(RequestEvent.id)
        )
    ).scalars().all()
    assert [event.action for event in events] == ["created", "claimed", "closed"]
    assert [event.actor_id for event in events] == [resident.id, worker.id, worker.id]


@pytest.mark.asyncio
async def test_assignment_records_reassignment_and_dispatcher(session):
    resident = User(telegram_id=10003, role="resident", is_approved=True)
    dispatcher = User(telegram_id=10004, role="dispatcher", is_approved=True)
    first = User(
        telegram_id=10005,
        role="worker",
        is_approved=True,
        worker_category="security",
    )
    second = User(
        telegram_id=10006,
        role="worker",
        is_approved=True,
        worker_category="security",
    )
    session.add_all([resident, dispatcher, first, second])
    await session.flush()
    request = await create_request(session, resident.id, "security", "Шум")

    assert (await assign_request(session, request.id, first.id, dispatcher))[0]
    assert (await assign_request(session, request.id, second.id, dispatcher))[0]

    events = (
        await session.execute(
            select(RequestEvent)
            .where(RequestEvent.request_id == request.id)
            .order_by(RequestEvent.id)
        )
    ).scalars().all()
    assert [event.action for event in events] == ["created", "assigned", "reassigned"]
    assert events[-1].actor_id == dispatcher.id
    assert f"previous_worker_id={first.id}" in events[-1].details


@pytest.mark.asyncio
async def test_delete_retains_audit_history(session):
    resident = User(telegram_id=10007, role="resident", is_approved=True)
    session.add(resident)
    await session.flush()
    request = await create_request(session, resident.id, "electrician", "Нет света")
    request_id = request.id

    assert (await delete_request(session, request_id, resident))[0]
    assert (
        await session.execute(select(Request).where(Request.id == request_id))
    ).scalar_one_or_none() is None
    events = (
        await session.execute(
            select(RequestEvent)
            .where(RequestEvent.request_id == request_id)
            .order_by(RequestEvent.id)
        )
    ).scalars().all()
    assert [event.action for event in events] == ["created", "deleted"]
