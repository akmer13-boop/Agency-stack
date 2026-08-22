from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.services.rop_b2c_mvp_dashboard import B2CPeriodFlow
from app.services.rop_calendar_report import (
    RopCalendarReport,
    build_rop_calendar_window,
    format_rop_calendar_report,
)
from app.services.rop_scheduler import RopSchedulerJobKind


def test_daily_window_is_previous_moscow_calendar_day() -> None:
    window = build_rop_calendar_window(
        RopSchedulerJobKind.DAILY,
        period_key="2026-08-23",
        timezone_name="Europe/Moscow",
    )

    assert window.local_start == date(2026, 8, 22)
    assert window.local_end == date(2026, 8, 22)
    assert window.start_at == datetime(
        2026,
        8,
        21,
        21,
        0,
        tzinfo=UTC,
    )
    assert window.end_at == datetime(
        2026,
        8,
        22,
        20,
        59,
        59,
        999999,
        tzinfo=UTC,
    )


def test_monday_weekly_window_includes_previous_weekend() -> None:
    window = build_rop_calendar_window(
        RopSchedulerJobKind.WEEKLY,
        period_key="2026-W35",
        timezone_name="Europe/Moscow",
    )

    assert window.local_start == date(2026, 8, 17)
    assert window.local_end == date(2026, 8, 23)


def test_calendar_report_separates_period_results_from_current_backlog() -> None:
    window = build_rop_calendar_window(
        RopSchedulerJobKind.WEEKLY,
        period_key="2026-W35",
        timezone_name="Europe/Moscow",
    )
    flow = B2CPeriodFlow(
        window_start=window.start_at,
        window_end=window.end_at,
        new_deals=25,
        won=7,
        lost=3,
    )
    first_response = SimpleNamespace(
        b2c_proven=40,
        breach=8,
        blocked=5,
        open=1,
        closed_measured=30,
        ok_share_closed_percent=73.3,
    )
    stage = SimpleNamespace(
        attention=17,
        blocked=9,
        blocked_reasons=(
            ("return_to_client_date_not_configured", 6),
            ("evidence_missing", 3),
        ),
        by_stage=(
            ("C7:EXECUTING", 20, 2, 12, 6),
            ("C7:FINAL_INVOICE", 6, 0, 0, 6),
        ),
    )
    current = SimpleNamespace(
        cutoff_at=datetime(2026, 8, 24, 7, 0, tzinfo=UTC),
        active_b2c_deals=120,
        stage_sla=stage,
    )

    text = format_rop_calendar_report(
        RopCalendarReport(
            window=window,
            flow=flow,
            first_response=first_response,
            current=current,
        )
    )

    assert text.startswith("ИИ-РОП · итоги недели\n")
    assert "Период: 17–23.08.2026 · МСК" in text
    assert "• подтверждённые B2C-лиды: 40" in text
    assert "• новые B2C-сделки: 25" in text
    assert "• WON / LOST: 7 / 3 · конверсия 70.0% (n=10)" in text
    assert "Текущий backlog" in text
    assert (
        "Stage SLA · подтверждённый срез на "
        "24.08.2026 10:00 МСК"
        in text
    )
    assert "• активные B2C-сделки: 120" in text
    assert "• требуют внимания: 17 · недостаточно данных: 3" in text
    assert "• SLA пока не настроен: 6" in text
    assert "• главная проблемная стадия: КП отправлено (12)" in text
    assert "выручка" not in text.casefold()
    assert "BITRIX WRITES = NONE" in text
