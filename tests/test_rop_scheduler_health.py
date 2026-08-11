import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.observability import JsonFormatter
from app.services.rop_scheduler import RopSchedulerState
from app.services.rop_scheduler_health import (
    RopSchedulerHealthStatus,
    RopSchedulerHealthStore,
    build_rop_scheduler_health,
    format_rop_scheduler_health,
)


def _ready_settings(tmp_path: Path, *, poll_seconds: int = 30) -> Settings:
    return Settings(
        _env_file=None,
        rop_scheduler_enabled=True,
        rop_scheduler_daily_enabled=True,
        rop_scheduler_daily_time="08:00",
        rop_scheduler_recipient_ids="100",
        telegram_manager_user_ids="100",
        rop_timezone="UTC",
        rop_scheduler_poll_seconds=poll_seconds,
        rop_scheduler_health_path=str(tmp_path / "health.json"),
    )


def test_disabled_scheduler_health_is_config_driven(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rop_scheduler_health_path=str(tmp_path / "health.json"),
    )
    report = build_rop_scheduler_health(settings)

    assert report.status is RopSchedulerHealthStatus.DISABLED
    assert report.scheduler_state is RopSchedulerState.DISABLED
    assert report.reason == "scheduler_disabled"


def test_blocked_scheduler_health_exposes_configuration_blocker(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rop_scheduler_enabled=True,
        rop_scheduler_health_path=str(tmp_path / "health.json"),
    )
    report = build_rop_scheduler_health(settings)

    assert report.status is RopSchedulerHealthStatus.BLOCKED
    assert "recipient_ids_missing" in report.reason


def test_ready_scheduler_without_runtime_state_is_not_started(tmp_path: Path) -> None:
    report = build_rop_scheduler_health(_ready_settings(tmp_path))

    assert report.status is RopSchedulerHealthStatus.NOT_STARTED
    assert report.reason == "runtime_state_missing"


def test_startup_without_completed_tick_is_starting(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path)
    store = RopSchedulerHealthStore(settings.rop_scheduler_health_path)
    started = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)

    store.record_startup(RopSchedulerState.READY, at=started)
    report = build_rop_scheduler_health(
        settings,
        now=datetime(2026, 8, 11, 18, 0, 20, tzinfo=UTC),
    )

    assert report.status is RopSchedulerHealthStatus.STARTING
    assert report.process_started_at == started


def test_fresh_successful_tick_is_healthy(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path)
    store = RopSchedulerHealthStore(settings.rop_scheduler_health_path)
    started = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)

    store.record_startup(RopSchedulerState.READY, at=started)
    store.record_tick_started(
        RopSchedulerState.READY,
        at=datetime(2026, 8, 11, 18, 0, 5, tzinfo=UTC),
    )
    store.record_tick_completed(
        RopSchedulerState.READY,
        due=1,
        delivered=1,
        failed=0,
        at=datetime(2026, 8, 11, 18, 0, 6, tzinfo=UTC),
    )

    report = build_rop_scheduler_health(
        settings,
        now=datetime(2026, 8, 11, 18, 0, 30, tzinfo=UTC),
    )

    assert report.status is RopSchedulerHealthStatus.HEALTHY
    assert report.last_tick_age_seconds == 24
    assert report.last_tick_delivered == 1
    assert report.consecutive_failures == 0


def test_partial_tick_is_degraded(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path)
    store = RopSchedulerHealthStore(settings.rop_scheduler_health_path)
    started = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)

    store.record_startup(RopSchedulerState.READY, at=started)
    store.record_tick_completed(
        RopSchedulerState.READY,
        due=2,
        delivered=1,
        failed=1,
        at=datetime(2026, 8, 11, 18, 0, 10, tzinfo=UTC),
    )

    report = build_rop_scheduler_health(
        settings,
        now=datetime(2026, 8, 11, 18, 0, 20, tzinfo=UTC),
    )

    assert report.status is RopSchedulerHealthStatus.DEGRADED
    assert report.last_tick_failed == 1
    assert report.consecutive_failures == 1


def test_tick_error_is_degraded_and_keeps_error_metadata(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path)
    store = RopSchedulerHealthStore(settings.rop_scheduler_health_path)
    started = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)

    store.record_startup(RopSchedulerState.READY, at=started)
    store.record_tick_error(
        RopSchedulerState.READY,
        error_code="tick_failed",
        at=datetime(2026, 8, 11, 18, 0, 10, tzinfo=UTC),
    )

    report = build_rop_scheduler_health(
        settings,
        now=datetime(2026, 8, 11, 18, 0, 20, tzinfo=UTC),
    )
    runtime = store.read()

    assert report.status is RopSchedulerHealthStatus.DEGRADED
    assert runtime is not None
    assert runtime.last_error_code == "tick_failed"
    assert runtime.consecutive_failures == 1


def test_old_tick_becomes_stale_using_technical_poll_window(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path, poll_seconds=30)
    store = RopSchedulerHealthStore(settings.rop_scheduler_health_path)
    started = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)

    store.record_startup(RopSchedulerState.READY, at=started)
    store.record_tick_completed(
        RopSchedulerState.READY,
        due=0,
        delivered=0,
        failed=0,
        at=datetime(2026, 8, 11, 18, 0, 5, tzinfo=UTC),
    )

    report = build_rop_scheduler_health(
        settings,
        now=datetime(2026, 8, 11, 18, 2, tzinfo=UTC),
    )

    assert report.stale_after_seconds == 90
    assert report.status is RopSchedulerHealthStatus.STALE


def test_unreadable_health_file_is_fail_safe(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path)
    Path(settings.rop_scheduler_health_path).write_text("{broken", encoding="utf-8")

    report = build_rop_scheduler_health(settings)

    assert report.status is RopSchedulerHealthStatus.UNAVAILABLE
    assert report.reason == "health_state_unreadable"


def test_health_formatter_does_not_claim_business_sla(tmp_path: Path) -> None:
    report = build_rop_scheduler_health(_ready_settings(tmp_path))
    text = format_rop_scheduler_health(report)

    assert "техническое здоровье scheduler" in text
    assert "не SLA бизнеса" in text


def test_json_formatter_preserves_scheduler_observability_fields() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="tick",
        args=(),
        exc_info=None,
    )
    record.event = "rop_scheduler_tick"
    record.state = "ready"
    record.due = 2
    record.delivered = 1
    record.failed = 1
    record.job = "rop_daily"
    record.period_key = "2026-08-11"
    record.recipient_id = 100
    record.health_status = "degraded"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "rop_scheduler_tick"
    assert payload["state"] == "ready"
    assert payload["due"] == 2
    assert payload["delivered"] == 1
    assert payload["failed"] == 1
    assert payload["job"] == "rop_daily"
    assert payload["period_key"] == "2026-08-11"
    assert payload["recipient_id"] == 100
    assert payload["health_status"] == "degraded"
