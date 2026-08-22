from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.config import Settings
from app.services import rop_b2c_mvp_dashboard as dashboard_module
from app.services.rop_b2c_mvp_dashboard import (
    _percent,
    format_b2c_mvp_dashboard,
    format_b2c_mvp_summary,
)


def _dashboard() -> SimpleNamespace:
    first_response = SimpleNamespace(
        b2c_proven=100,
        measured=80,
        measured_share_percent=80.0,
        ok=60,
        breach=18,
        open=2,
        blocked=20,
        ok_share_closed_percent=76.9,
        closed_measured=78,
    )

    stage_deals = (
        SimpleNamespace(
            status="ATTENTION",
            deal_id=101,
            stage_label="КП отправлено",
            manager_name="Анна Тестова",
            deadline_at=datetime(
                2026,
                8,
                21,
                12,
                0,
                tzinfo=UTC,
            ),
        ),
    )

    stage_sla = SimpleNamespace(
        tracked_deals=42,
        open=10,
        attention=25,
        blocked=7,
        blocked_reasons=(
            (
                "return_to_client_date_not_configured",
                4,
            ),
            (
                "successful_call_exact_reset_missing",
                3,
            ),
        ),
        by_stage=(
            (
                "C7:EXECUTING",
                35,
                10,
                22,
                3,
            ),
            (
                "C7:FINAL_INVOICE",
                4,
                0,
                0,
                4,
            ),
        ),
        deals=stage_deals,
    )

    managers = (
        SimpleNamespace(
            manager_name="Анна Тестова",
            stage_attention=9,
            first_response_breaches=2,
            active_deals=15,
            month_won=3,
            month_lost=4,
        ),
    )

    return SimpleNamespace(
        cutoff_at=datetime(
            2026,
            8,
            22,
            12,
            43,
            tzinfo=UTC,
        ),
        active_b2c_deals=50,
        month_new_deals=12,
        month_won=7,
        month_lost=3,
        closed_conversion_percent=70.0,
        won_revenue_by_currency=(),
        first_response=first_response,
        stage_sla=stage_sla,
        managers=managers,
    )


def test_dashboard_uses_human_mvp_language() -> None:
    text = format_b2c_mvp_dashboard(
        _dashboard()
    )

    assert "ИИ-РОП · B2C Dashboard" in text
    assert "требуют внимания: 25" in text
    assert (
        "недостаточно данных для безопасной оценки: 3"
        in text
    )
    assert (
        "Потенциальный клиент: 4 · SLA пока не настроен"
        in text
    )
    assert "BLOCKED" not in text
    assert "successful_call_exact_reset_missing" not in text
    assert "return_to_client_date_not_configured" not in text


def test_dashboard_summary_is_compact_and_keeps_truth_metrics() -> None:
    text = format_b2c_mvp_summary(
        _dashboard()
    )

    assert text.startswith("ИИ-РОП · B2C\n")
    assert "• лиды: 100 · новые сделки: 12" in text
    assert "• WON / LOST: 7 / 3 · конверсия 70.0% (n=10)" in text
    assert "• соблюдение: 76.9% (n=78)" in text
    assert "• нарушений: 18 · недостаточно данных: 20" in text
    assert "• требуют внимания: 25 · недостаточно данных: 3" in text
    assert "• SLA пока не настроен: 4" in text
    assert "• КП отправлено: 22 требуют внимания" in text
    assert "ИИ-анализ" in text
    assert "Менеджеры · приоритет разбора" not in text
    assert "Самые просроченные сделки" not in text
    assert "выручка" not in text


def test_percent_is_safe_for_empty_denominator() -> None:
    assert _percent(7, 10) == 70.0
    assert _percent(1, 0) == 0.0


def test_period_flow_uses_exact_calendar_boundaries(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_module,
        "_eligible_deals",
        lambda _database_path: (
            {
                1: {
                    "DATE_CREATE": "2026-08-21T21:00:00+00:00",
                    "STAGE_SEMANTIC_ID": "P",
                },
                2: {
                    "DATE_CREATE": "2026-08-01T10:00:00+00:00",
                    "STAGE_SEMANTIC_ID": "S",
                    "MOVED_TIME": "2026-08-22T10:00:00+00:00",
                },
                3: {
                    "DATE_CREATE": "2026-08-22T20:59:59+00:00",
                    "STAGE_SEMANTIC_ID": "F",
                    "MOVED_TIME": "2026-08-22T21:00:00+00:00",
                },
            },
            {},
        ),
    )

    flow = dashboard_module.build_b2c_period_flow(
        Settings(_env_file=None),
        window_start=datetime(2026, 8, 21, 21, 0, tzinfo=UTC),
        window_end=datetime(
            2026,
            8,
            22,
            20,
            59,
            59,
            tzinfo=UTC,
        ),
    )

    assert flow.new_deals == 2
    assert flow.won == 1
    assert flow.lost == 0
    assert flow.conversion_percent == 100.0
