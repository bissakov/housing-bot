from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from bot.models import Request, RequestAttachment, RequestEvent, User, Announcement
from bot.auth import is_administrator, is_dispatcher
from bot.constants import CATEGORY_LABELS, REQUEST_CATEGORIES, URGENCY_LEVELS
from bot.timezone import utc_now
from bot.services.schedules import is_worker_available
from bot.i18n import t
from bot.services.request_routing import is_worker_ready


def _event(
    request_id: int,
    action: str,
    actor_id: int | None = None,
    details: str | None = None,
) -> RequestEvent:
    return RequestEvent(
        request_id=request_id,
        actor_id=actor_id,
        action=action,
        details=details,
    )


async def create_request(
    session: AsyncSession,
    resident_id: int,
    category: str,
    description: str,
    urgency: str | None = None,
    raw_description: str | None = None,
    llm_meta: str | None = None,
    *,
    service_area: str | None = None,
    dispatch_after=None,
    attachments: list[dict[str, str]] | None = None,
) -> Request:
    if category not in REQUEST_CATEGORIES:
        raise ValueError("Unsupported request category")
    if urgency is not None and urgency not in URGENCY_LEVELS:
        raise ValueError("Unsupported urgency")
    if service_area not in {None, "apartment", "common"}:
        raise ValueError("Unsupported service area")
    req = Request(
        resident_id=resident_id,
        category=category,
        description=description,
        status="new",
        urgency=urgency,
        raw_description=raw_description,
        llm_meta=llm_meta,
        service_area=service_area,
        approval_status="pending" if category == "kazakhdomofon" else None,
        dispatch_after=dispatch_after,
    )
    session.add(req)
    await session.flush()
    for attachment in attachments or []:
        session.add(RequestAttachment(request_id=req.id, **attachment))
    session.add(_event(req.id, "created", resident_id, f"category={category}"))
    await session.flush()
    return req


async def claim_request(session: AsyncSession, request_id: int, worker: User) -> tuple[bool, str]:
    """Atomic claim: only if status==new. Returns (success, message)."""
    result = await session.execute(
        select(Request).where(Request.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        return False, "Заявка не найдена"
    if worker.role != "worker" or not worker.is_approved:
        return False, "Недостаточно прав"
    if req.status != "new":
        return False, "Заявка уже принята другим исполнителем"
    if req.category != worker.worker_category:
        return False, "Категория заявки не совпадает с вашей специализацией"
    if not is_worker_ready(req, utc_now()):
        return False, "Заявка ещё не направлена на исполнение"
    if not worker.is_on_shift:
        return False, "Вы не на смене"
    if not await is_worker_available(session, worker):
        return False, t("not_scheduled_claim", worker.language)

    upd = await session.execute(
        update(Request)
        .where(Request.id == request_id, Request.status == "new")
        .values(status="accepted", worker_id=worker.id, accepted_at=utc_now())
    )
    if upd.rowcount == 0:
        return False, "Заявка уже принята другим исполнителем"
    session.add(_event(request_id, "claimed", worker.id))
    await session.flush()
    await session.refresh(req)
    return True, "ok"


async def close_request(
    session: AsyncSession,
    request_id: int,
    actor: User,
    *,
    completion_result: str | None = None,
    completion_comment: str | None = None,
    completion_raw_comment: str | None = None,
    completion_llm_meta: str | None = None,
) -> tuple[bool, str]:
    """Complete an accepted request with a done/not-done result.

    The function name and persisted ``closed`` status are retained for database
    and API compatibility; user-facing copy consistently calls this completion.
    """
    result = await session.execute(select(Request).where(Request.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        return False, "Заявка не найдена"
    if req.status != "accepted":
        return False, "Можно завершить только заявку в работе"
    if actor.role == "worker" and req.worker_id != actor.id:
        return False, "Это не ваша заявка"
    if actor.role != "worker" and not is_dispatcher(actor):
        return False, "Недостаточно прав"
    if completion_result not in {"done", "not_done"}:
        return False, "Выберите результат: выполнено или не выполнено"
    if not (completion_comment or "").strip():
        return False, "Комментарий обязателен"
    conditions = [Request.id == request_id, Request.status == "accepted"]
    if actor.role == "worker":
        conditions.append(Request.worker_id == actor.id)
    upd = await session.execute(
        update(Request).where(*conditions).values(
            status="closed",
            closed_at=utc_now(),
            completion_result=completion_result,
            completion_comment=(completion_comment or "").strip() or None,
            completion_raw_comment=(completion_raw_comment or "").strip() or None,
            completion_llm_meta=completion_llm_meta,
        )
    )
    if upd.rowcount == 0:
        return False, "Заявка уже завершена или недоступна"
    details = f"completion_result={completion_result}" if completion_result else None
    session.add(_event(request_id, "closed", actor.id, details))
    await session.flush()
    return True, "ok"


async def assign_request(
    session: AsyncSession,
    request_id: int,
    worker_user_id: int,
    actor: User | None = None,
) -> tuple[bool, str]:
    result = await session.execute(select(Request).where(Request.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        return False, "Заявка не найдена"
    if req.status not in ("new", "accepted"):
        return False, "Завершённую заявку нельзя назначить"
    if req.category == "cleaning":
        return False, "Заявки клининга принимает только клининг"
    if not is_worker_ready(req, utc_now()):
        return False, "Заявка ещё не направлена на исполнение"
    wres = await session.execute(select(User).where(User.id == worker_user_id))
    worker = wres.scalar_one_or_none()
    if not worker or worker.role != "worker":
        return False, "Исполнитель не найден"
    if req.category != worker.worker_category:
        request_category = CATEGORY_LABELS.get(req.category, "Неизвестная категория")
        worker_category = CATEGORY_LABELS.get(
            worker.worker_category, "Неизвестная категория"
        )
        return False, (
            f"Категория заявки ({request_category}) не совпадает с категорией "
            f"исполнителя ({worker_category})"
        )
    previous_worker_id = req.worker_id
    conditions = [Request.id == request_id, Request.status.in_(("new", "accepted"))]
    conditions.append(
        Request.worker_id.is_(None)
        if previous_worker_id is None
        else Request.worker_id == previous_worker_id
    )
    upd = await session.execute(
        update(Request).where(*conditions).values(
            worker_id=worker.id,
            status="accepted",
            accepted_at=utc_now(),
        )
    )
    if upd.rowcount != 1:
        return False, "Заявка уже была изменена другим диспетчером"
    action = "reassigned" if previous_worker_id is not None else "assigned"
    details = f"worker_id={worker.id}"
    if previous_worker_id is not None:
        details += f";previous_worker_id={previous_worker_id}"
    session.add(_event(request_id, action, actor.id if actor else None, details))
    await session.flush()
    return True, "ok"


async def approve_request(
    session: AsyncSession, request_id: int, actor: User
) -> tuple[bool, str]:
    """Approve a Kazakhdomofon request for the normal worker queue."""
    if not is_administrator(actor):
        return False, "Только председатель может согласовать заявку"
    result = await session.execute(select(Request).where(Request.id == request_id))
    req = result.scalar_one_or_none()
    if not req or req.category != "kazakhdomofon":
        return False, "Заявка Казахдомофон не найдена"
    if req.status != "new" or req.approval_status != "pending":
        return False, "Заявка уже рассмотрена"
    now = utc_now()
    updated = await session.execute(
        update(Request)
        .where(
            Request.id == request_id,
            Request.status == "new",
            Request.approval_status == "pending",
        )
        .values(
            approval_status="approved",
            approval_comment=None,
            reviewed_by_id=actor.id,
            reviewed_at=now,
        )
    )
    if updated.rowcount != 1:
        return False, "Заявка уже рассмотрена"
    session.add(_event(request_id, "approved", actor.id))
    await session.flush()
    return True, "ok"


async def reject_request(
    session: AsyncSession, request_id: int, actor: User, comment: str
) -> tuple[bool, str]:
    """Reject a pending request with a required chairman comment."""
    comment = comment.strip()
    if not is_administrator(actor):
        return False, "Только председатель может отклонить заявку"
    if not comment:
        return False, "Комментарий обязателен"
    result = await session.execute(select(Request).where(Request.id == request_id))
    req = result.scalar_one_or_none()
    if not req or req.category != "kazakhdomofon":
        return False, "Заявка Казахдомофон не найдена"
    if req.status != "new" or req.approval_status != "pending":
        return False, "Заявка уже рассмотрена"
    now = utc_now()
    updated = await session.execute(
        update(Request)
        .where(
            Request.id == request_id,
            Request.status == "new",
            Request.approval_status == "pending",
        )
        .values(
            status="closed",
            approval_status="rejected",
            approval_comment=comment,
            reviewed_by_id=actor.id,
            reviewed_at=now,
            closed_at=now,
            completion_result="not_done",
            completion_comment=comment,
        )
    )
    if updated.rowcount != 1:
        return False, "Заявка уже рассмотрена"
    session.add(_event(request_id, "rejected", actor.id, comment[:500]))
    await session.flush()
    return True, "ok"


async def delete_request(session: AsyncSession, request_id: int, actor: User) -> tuple[bool, str]:
    """Administrators can delete any; residents only their own new requests."""
    result = await session.execute(select(Request).where(Request.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        return False, "Заявка не найдена"

    is_owner = req.resident_id == actor.id

    if is_administrator(actor):
        details = "by=administrator"
    elif is_owner and req.status == "new":
        details = "by=resident"
    else:
        if is_owner:
            return False, "Можно удалить только новую заявку (не в работе)"
        return False, "Недостаточно прав для удаления"

    session.add(_event(request_id, "deleted", actor.id, details))
    await session.execute(delete(Request).where(Request.id == request_id))
    await session.flush()
    return True, "ok"


async def delete_announcement(session: AsyncSession, ann_id: int, actor: User) -> tuple[bool, str]:
    if not is_administrator(actor):
        return False, "Только администратор может удалять объявления"

    result = await session.execute(select(Announcement).where(Announcement.id == ann_id))
    ann = result.scalar_one_or_none()
    if not ann:
        return False, "Объявление не найдено"
    await session.execute(delete(Announcement).where(Announcement.id == ann_id))
    await session.flush()
    return True, "ok"


async def get_requests_for_worker(session: AsyncSession, category: str, status: str = "new", limit: int = 20, offset: int = 0):
    result = await session.execute(
        select(Request).where(Request.category == category, Request.status == status)
        .order_by(Request.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


async def get_requests_for_resident(session: AsyncSession, resident_db_id: int):
    result = await session.execute(
        select(Request).where(Request.resident_id == resident_db_id).order_by(Request.created_at.desc())
    )
    return result.scalars().all()


async def create_announcement(session: AsyncSession, author_id: int, text: str) -> Announcement:
    ann = Announcement(author_id=author_id, text=text)
    session.add(ann)
    await session.flush()
    return ann
