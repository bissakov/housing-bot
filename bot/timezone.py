"""UTC-to-local conversion for timestamps shown to users."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.config import DISPLAY_TIMEZONE


_display_timezone = ZoneInfo(DISPLAY_TIMEZONE)


def utc_now() -> datetime:
    """Return the current instant in UTC from the application's central clock."""
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    """Return the current instant in the configured display timezone."""
    return utc_now().astimezone(_display_timezone)


def localize(value: datetime) -> datetime:
    """Convert a stored UTC timestamp to the configured display timezone.

    SQLite returns timezone-aware columns as naive values, while PostgreSQL
    returns aware values. Stored naive values are therefore interpreted as UTC.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_display_timezone)


def format_local(value: datetime | None, fmt: str, fallback: str = "—") -> str:
    """Format a timestamp in the configured display timezone."""
    if value is None:
        return fallback
    return localize(value).strftime(fmt)
