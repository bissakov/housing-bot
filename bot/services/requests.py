from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from bot.models import Request, RequestEvent, User, Announcement
from bot.auth import is_administrator, is_dispatcher
from bot.constants import REQUEST_CATEGORIES, URGENCY_LEVELS
from bot.timezone import utc_now
from bot.services.schedules import is_worker_available
from bot.i18n import t


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


async def create_request(session: AsyncSession, resident_id: int, category: str, description: str, urgency: str | None = None, raw_description: str | None = None, llm_meta: str | None = None) -> Request:
    if category not in REQUEST_CATEGORIES:
        raise ValueError("Unsupported request category")
    if urgency is not None and urgency not in URGENCY_LEVELS:
        raise ValueError("Unsupported urgency")
    req = Request(resident_id=resident_id, category=category, description=description, status="new", urgency=urgency, raw_description=raw_description, llm_meta=llm_meta)
    session.add(req)
    await session.flush()
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
    result = await session.execute(select(Request).where(Request.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        return False, "Заявка не найдена"
    if req.status != "accepted":
        return False, "Можно закрыть только заявку в работе"
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
        return False, "Заявка уже закрыта или недоступна"
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
        return False, "Закрытую заявку нельзя назначить"
    wres = await session.execute(select(User).where(User.id == worker_user_id))
    worker = wres.scalar_one_or_none()
    if not worker or worker.role != "worker":
        return False, "Исполнитель не найден"
    if req.category != worker.worker_category:
        return False, f"Категория заявки ({req.category}) не совпадает с категорией исполнителя ({worker.worker_category})"
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
