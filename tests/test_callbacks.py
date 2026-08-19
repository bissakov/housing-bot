import pytest
from pydantic import ValidationError

from bot.callbacks import (
    DispatcherRequestCallback,
    DispatcherHistoryCallback,
    ResidentRequestCallback,
    WorkerAssignedCallback,
    WorkerAvailableCallback,
)


@pytest.mark.parametrize(
    ("callback_type", "prefix"),
    [
        (WorkerAvailableCallback, "w_av_view"),
        (WorkerAssignedCallback, "w_my_view"),
        (ResidentRequestCallback, "res_view"),
        (DispatcherRequestCallback, "req_view"),
        (DispatcherHistoryCallback, "req_history"),
    ],
)
def test_request_callback_round_trip(callback_type, prefix):
    packed = callback_type(request_id=42, page=3).pack()
    assert packed == f"{prefix}:42:3"
    unpacked = callback_type.unpack(packed)
    assert unpacked.request_id == 42
    assert unpacked.page == 3


def test_typed_callback_rejects_malformed_integer():
    with pytest.raises((ValueError, ValidationError)):
        WorkerAvailableCallback.unpack("w_av_view:not-an-id:0")
