from datetime import date

from bot.callbacks import ReportCallback
from bot.services.reports import ReportFilters, _csv_safe, build_overview, export_csv, report_keyboard, report_period


def test_report_callbacks_fit_telegram_limit_with_all_filters():
    filters = ReportFilters(
        period="custom",
        category="electrician",
        urgency="normal",
        worker_id=123456789,
        result="not_done",
        escalation="yes",
        custom_start=date(2025, 1, 1),
        custom_end=date(2026, 1, 1),
    )
    keyboard = report_keyboard(filters, 5)
    callback_values = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert callback_values
    assert all(len(value.encode()) <= 64 for value in callback_values)
    assert all(ReportCallback.unpack(value) for value in callback_values)


def test_report_period_uses_inclusive_custom_dates():
    start, end, label = report_period(
        ReportFilters(period="custom", custom_start=date(2026, 3, 1), custom_end=date(2026, 3, 7))
    )

    assert start < end
    assert (end - start).days == 7
    assert label == "01.03.2026–07.03.2026"


def test_csv_values_cannot_become_spreadsheet_formulas():
    assert _csv_safe("=1+1") == "'=1+1"
    assert _csv_safe("+123") == "'+123"
    assert _csv_safe("normal text") == "normal text"


async def test_overview_uses_completion_wording(session):
    text, _ = await build_overview(
        session,
        ReportFilters(period="custom", custom_start=date(2000, 1, 1), custom_end=date(2000, 1, 2)),
    )

    assert "Завершено" in text
    assert "Закрыто" not in text


async def test_empty_csv_export_has_headers(session):
    document = await export_csv(
        session,
        ReportFilters(period="custom", custom_start=date(2000, 1, 1), custom_end=date(2000, 1, 2)),
    )

    assert document.data.startswith(b"\xef\xbb\xbf")
    assert "Создана".encode() in document.data
    assert document.filename.startswith("заявки_")
