"""Centralized role and resource authorization helpers."""

from bot.config import ADMIN_IDS, DEV_MODE
from bot.models import Request, User


def is_dispatcher(user: User | None) -> bool:
    if user is None:
        return False
    if user.role == "dispatcher":
        return True
    # Configured production admins retain emergency dispatcher access. In
    # development they must explicitly have the dispatcher role so tests do
    # not accidentally grant broad permissions.
    return not DEV_MODE and user.telegram_id in ADMIN_IDS


def is_approved_resident(user: User | None) -> bool:
    return bool(user and user.role == "resident" and user.is_approved)


def is_approved_worker(user: User | None) -> bool:
    return bool(user and user.role == "worker" and user.is_approved)


def can_view_available_request(user: User | None, request: Request) -> bool:
    return bool(
        is_approved_worker(user)
        and user.is_on_shift
        and request.status == "new"
        and request.category == user.worker_category
    )


def can_view_assigned_request(user: User | None, request: Request) -> bool:
    return bool(is_approved_worker(user) and request.worker_id == user.id)
