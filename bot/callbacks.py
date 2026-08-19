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
