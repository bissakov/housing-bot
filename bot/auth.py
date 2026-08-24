"""Centralized role and resource authorization helpers."""

from bot.models import Request, User
from bot.services.request_routing import is_worker_ready
from bot.timezone import utc_now


def is_administrator(user: User | None) -> bool:
    """Return whether the user has the highest system role."""
    return bool(user and user.role == "administrator" and user.is_approved)


def is_dispatcher(user: User | None) -> bool:
    """Return whether the user has dispatcher-level access or higher."""
    return bool(
        user
        and user.is_approved
        and user.role in {"dispatcher", "administrator"}
    )


def is_approved_resident(user: User | None) -> bool:
    return bool(user and user.role == "resident" and user.is_approved)


def is_approved_owner(user: User | None) -> bool:
    return bool(
        user
        and user.role == "resident"
        and user.resident_subrole == "owner"
        and user.is_approved
    )


def is_approved_worker(user: User | None) -> bool:
    return bool(user and user.role == "worker" and user.is_approved)


def can_view_available_request(user: User | None, request: Request) -> bool:
    return bool(
        is_approved_worker(user)
        and user.is_on_shift
        and request.status == "new"
        and request.category == user.worker_category
        and is_worker_ready(request, utc_now())
    )


def can_view_assigned_request(user: User | None, request: Request) -> bool:
    return bool(is_approved_worker(user) and request.worker_id == user.id)
