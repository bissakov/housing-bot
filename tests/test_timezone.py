from datetime import datetime, timedelta, timezone

from bot.timezone import format_local, localize


def test_format_local_converts_utc_to_almaty_time():
    value = datetime(2026, 3, 24, 12, 30, tzinfo=timezone.utc)

    assert format_local(value, "%d.%m.%Y %H:%M") == "24.03.2026 17:30"


def test_localize_treats_sqlite_naive_timestamp_as_utc():
    value = datetime(2026, 3, 24, 12, 30)

    localized = localize(value)

    assert localized.hour == 17
    assert localized.utcoffset() == timedelta(hours=5)


def test_format_local_supports_fallback_for_missing_timestamp():
    assert format_local(None, "%H:%M") == "—"
    assert format_local(None, "%H:%M", "") == ""
