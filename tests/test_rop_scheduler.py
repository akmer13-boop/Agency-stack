from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.services.rop_scheduler import (
    RopSchedulerLedger,
    RopSchedulerState,
    build_rop_scheduler_plan,
    due_rop_scheduler_deliveries,
    format_rop_scheduler_plan,
)


def test_scheduler_is_disabled_by_default() -> None:
    settings = Settings(_env_file=None)
    plan = build_rop_scheduler_plan(settings)

    assert plan.state is RopSchedulerState.DISABLED
    assert plan.blockers == ("scheduler_disabled",)
    assert plan.jobs == ()


def test_enabled_scheduler_without_business_config_is_blocked() -> None:
    settings = Settings(_env_file=None, rop_scheduler_enabled=True)
    plan = build_rop_scheduler_plan(settings)

    assert plan.state is RopSchedulerState.BLOCKED
    assert "recipient_ids_missing" in plan.blockers
    assert "no_jobs_enabled" in plan.blockers


def test_daily_scheduler_ready_only_with_explicit_time_and_rop_recipient() -> None:
    settings = Settings(
        _env_file=None,
        rop_scheduler_enabled=True,
        rop_scheduler_daily_enabled=True,
        rop_scheduler_daily_time="08:30",
        rop_scheduler_recipient_ids="100",
        telegram_manager_user_ids="100",
        rop_timezone="UTC",
    )
    plan = build_rop_scheduler_plan(settings)

    assert plan.state is RopSchedulerState.READY
    assert len(plan.jobs) == 1
    assert plan.jobs[0].name == "rop_daily"
    assert plan.recipients == (100,)


def test_scheduler_blocks_recipient_without_rop_role() -> None:
    settings = Settings(
        _env_file=None,
        rop_scheduler_enabled=True,
        rop_scheduler_daily_enabled=True,
        rop_scheduler_daily_time="08:30",
        rop_scheduler_recipient_ids="100",
        telegram_allowed_user_ids="100",
        rop_timezone="UTC",
    )
    plan = build_rop_scheduler_plan(settings)

    assert plan.state is RopSchedulerState.BLOCKED
    assert "recipient_not_rop_role:100" in plan.blockers


def test_daily_delivery_is_due_once_per_recipient_and_period(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rop_scheduler_enabled=True,
        rop_scheduler_daily_enabled=True,
        rop_scheduler_daily_time="08:30",
        rop_scheduler_recipient_ids="100,200",
        telegram_manager_user_ids="100,200",
        rop_timezone="UTC",
    )
    plan = build_rop_scheduler_plan(settings)
    ledger = RopSchedulerLedger(str(tmp_path / "scheduler.json"))
    now = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)

    first = due_rop_scheduler_deliveries(plan, ledger, now=now)
    assert len(first) == 2

    ledger.mark_delivered(first[0].ledger_key, delivered_at=now)

    second = due_rop_scheduler_deliveries(plan, ledger, now=now)
    assert len(second) == 1
    assert second[0].recipient_id == 200


def test_weekly_scheduler_catches_up_inside_same_week(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rop_scheduler_enabled=True,
        rop_scheduler_weekly_enabled=True,
        rop_scheduler_weekly_day="mon",
        rop_scheduler_weekly_time="09:00",
        rop_scheduler_recipient_ids="100",
        telegram_admin_user_ids="100",
        rop_timezone="UTC",
    )
    plan = build_rop_scheduler_plan(settings)
    ledger = RopSchedulerLedger(str(tmp_path / "scheduler.json"))

    tuesday = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    due = due_rop_scheduler_deliveries(plan, ledger, now=tuesday)

    assert len(due) == 1
    assert due[0].period_key == "2026-W33"


def test_scheduler_status_does_not_claim_automatic_defaults() -> None:
    plan = build_rop_scheduler_plan(Settings(_env_file=None))
    text = format_rop_scheduler_plan(plan)

    assert "state: disabled" in text
    assert "время и получатели не подставляются автоматически" in text
    assert "default state = DISABLED" in text
