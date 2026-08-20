"""Typed callback payloads for safe Telegram callback parsing."""

from aiogram.filters.callback_data import CallbackData


class WorkerAvailableCallback(CallbackData, prefix="w_av_view"):
    request_id: int
    page: int = 0


class WorkerAssignedCallback(CallbackData, prefix="w_my_view"):
    request_id: int
    page: int = 0


class ResidentRequestCallback(CallbackData, prefix="res_view"):
    request_id: int
    page: int = 0


class DispatcherRequestCallback(CallbackData, prefix="req_view"):
    request_id: int
    page: int = 0


class DispatcherHistoryCallback(CallbackData, prefix="req_history"):
    request_id: int
    page: int = 0


class DispatcherFilteredRequestCallback(CallbackData, prefix="req_fview"):
    """Request card opened from a filtered dispatcher list."""

    request_id: int
    page: int = 0
    status: str = "all"
    category: str = "all"


class ReportCallback(CallbackData, prefix="rp"):
    """Compact report navigation and filter state."""

    action: str = "o"
    period: str = "7"
    category: str = "a"
    urgency: str = "a"
    worker_id: int = 0
    result: str = "a"
    escalation: str = "a"
    start_day: int = 0
    end_day: int = 0
