from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.config import Settings
from app.services.rop_scheduler import RopSchedulerState, build_rop_scheduler_plan


class RopSchedulerTickStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


class RopSchedulerHealthStatus(StrEnum):
    DISABLED = "disabled"
    BLOCKED = "blocked"
    NOT_STARTED = "not_started"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RopSchedulerRuntimeState:
    scheduler_state: str
    process_started_at: datetime | None
    last_tick_started_at: datetime | None
    last_tick_completed_at: datetime | None
    last_tick_status: str | None
    last_tick_due: int
    last_tick_delivered: int
    last_tick_failed: int
    last_delivery_at: datetime | None
    last_error_at: datetime | None
    last_error_code: str | None
    consecutive_failures: int


@dataclass(frozen=True, slots=True)
class RopSchedulerHealthReport:
    status: RopSchedulerHealthStatus
    scheduler_state: RopSchedulerState
    reason: str
    poll_seconds: int
    stale_after_seconds: int
    process_started_at: datetime | None
    last_tick_completed_at: datetime | None
    last_tick_age_seconds: int | None
    last_tick_status: str | None
    last_tick_due: int
    last_tick_delivered: int
    last_tick_failed: int
    last_delivery_at: datetime | None
    consecutive_failures: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _parse_datetime(value: object, *, field_name: str) -> datetime | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise RuntimeError(f"Scheduler health field {field_name} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"Scheduler health field {field_name} must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"Scheduler health field {field_name} must include timezone")
    return parsed.astimezone(UTC)


def _parse_int(value: object, *, field_name: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"Scheduler health field {field_name} must be an integer")
    if value < 0:
        raise RuntimeError(f"Scheduler health field {field_name} must be >= 0")
    return value


class RopSchedulerHealthStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def _read_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Scheduler health state is unreadable: {self.path}") from exc

        if not isinstance(payload, dict):
            raise RuntimeError(f"Scheduler health state has invalid schema: {self.path}")
        if payload.get("version", 1) != 1:
            raise RuntimeError(f"Scheduler health state has unsupported version: {self.path}")
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload["version"] = 1

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary_path = Path(temporary.name)

        temporary_path.replace(self.path)

    def read(self) -> RopSchedulerRuntimeState | None:
        payload = self._read_payload()
        if not payload:
            return None

        return RopSchedulerRuntimeState(
            scheduler_state=str(payload.get("scheduler_state", "")),
            process_started_at=_parse_datetime(
                payload.get("process_started_at"),
                field_name="process_started_at",
            ),
            last_tick_started_at=_parse_datetime(
                payload.get("last_tick_started_at"),
                field_name="last_tick_started_at",
            ),
            last_tick_completed_at=_parse_datetime(
                payload.get("last_tick_completed_at"),
                field_name="last_tick_completed_at",
            ),
            last_tick_status=(
                str(payload["last_tick_status"])
                if payload.get("last_tick_status") not in {None, ""}
                else None
            ),
            last_tick_due=_parse_int(
                payload.get("last_tick_due"),
                field_name="last_tick_due",
            ),
            last_tick_delivered=_parse_int(
                payload.get("last_tick_delivered"),
                field_name="last_tick_delivered",
            ),
            last_tick_failed=_parse_int(
                payload.get("last_tick_failed"),
                field_name="last_tick_failed",
            ),
            last_delivery_at=_parse_datetime(
                payload.get("last_delivery_at"),
                field_name="last_delivery_at",
            ),
            last_error_at=_parse_datetime(
                payload.get("last_error_at"),
                field_name="last_error_at",
            ),
            last_error_code=(
                str(payload["last_error_code"])
                if payload.get("last_error_code") not in {None, ""}
                else None
            ),
            consecutive_failures=_parse_int(
                payload.get("consecutive_failures"),
                field_name="consecutive_failures",
            ),
        )

    def record_startup(
        self,
        scheduler_state: RopSchedulerState,
        *,
        at: datetime,
    ) -> None:
        payload = self._read_payload()
        timestamp = _as_utc(at).isoformat()
        payload.update(
            {
                "scheduler_state": scheduler_state.value,
                "process_started_at": timestamp,
                "updated_at": timestamp,
            }
        )
        self._write_payload(payload)

    def record_tick_started(
        self,
        scheduler_state: RopSchedulerState,
        *,
        at: datetime,
    ) -> None:
        payload = self._read_payload()
        timestamp = _as_utc(at).isoformat()
        payload.update(
            {
                "scheduler_state": scheduler_state.value,
                "last_tick_started_at": timestamp,
                "updated_at": timestamp,
            }
        )
        self._write_payload(payload)

    def record_tick_completed(
        self,
        scheduler_state: RopSchedulerState,
        *,
        due: int,
        delivered: int,
        failed: int,
        at: datetime,
    ) -> None:
        if min(due, delivered, failed) < 0:
            raise ValueError("tick counters must be >= 0")

        payload = self._read_payload()
        timestamp = _as_utc(at).isoformat()
        previous_failures = _parse_int(
            payload.get("consecutive_failures"),
            field_name="consecutive_failures",
        )
        tick_status = RopSchedulerTickStatus.PARTIAL if failed > 0 else RopSchedulerTickStatus.OK

        payload.update(
            {
                "scheduler_state": scheduler_state.value,
                "last_tick_completed_at": timestamp,
                "last_tick_status": tick_status.value,
                "last_tick_due": due,
                "last_tick_delivered": delivered,
                "last_tick_failed": failed,
                "consecutive_failures": previous_failures + 1 if failed else 0,
                "updated_at": timestamp,
            }
        )

        if delivered > 0:
            payload["last_delivery_at"] = timestamp
        if failed > 0:
            payload["last_error_at"] = timestamp
            payload["last_error_code"] = "delivery_failed"

        self._write_payload(payload)

    def record_tick_error(
        self,
        scheduler_state: RopSchedulerState,
        *,
        error_code: str,
        at: datetime,
    ) -> None:
        payload = self._read_payload()
        timestamp = _as_utc(at).isoformat()
        previous_failures = _parse_int(
            payload.get("consecutive_failures"),
            field_name="consecutive_failures",
        )

        payload.update(
            {
                "scheduler_state": scheduler_state.value,
                "last_tick_completed_at": timestamp,
                "last_tick_status": RopSchedulerTickStatus.ERROR.value,
                "last_tick_due": 0,
                "last_tick_delivered": 0,
                "last_tick_failed": 0,
                "last_error_at": timestamp,
                "last_error_code": error_code,
                "consecutive_failures": previous_failures + 1,
                "updated_at": timestamp,
            }
        )
        self._write_payload(payload)


def build_rop_scheduler_health(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> RopSchedulerHealthReport:
    plan = build_rop_scheduler_plan(settings)
    stale_after_seconds = max(settings.rop_scheduler_poll_seconds * 3, 60)

    if plan.state is RopSchedulerState.DISABLED:
        return RopSchedulerHealthReport(
            status=RopSchedulerHealthStatus.DISABLED,
            scheduler_state=plan.state,
            reason="scheduler_disabled",
            poll_seconds=settings.rop_scheduler_poll_seconds,
            stale_after_seconds=stale_after_seconds,
            process_started_at=None,
            last_tick_completed_at=None,
            last_tick_age_seconds=None,
            last_tick_status=None,
            last_tick_due=0,
            last_tick_delivered=0,
            last_tick_failed=0,
            last_delivery_at=None,
            consecutive_failures=0,
        )

    if plan.state is RopSchedulerState.BLOCKED:
        return RopSchedulerHealthReport(
            status=RopSchedulerHealthStatus.BLOCKED,
            scheduler_state=plan.state,
            reason=",".join(plan.blockers) or "scheduler_blocked",
            poll_seconds=settings.rop_scheduler_poll_seconds,
            stale_after_seconds=stale_after_seconds,
            process_started_at=None,
            last_tick_completed_at=None,
            last_tick_age_seconds=None,
            last_tick_status=None,
            last_tick_due=0,
            last_tick_delivered=0,
            last_tick_failed=0,
            last_delivery_at=None,
            consecutive_failures=0,
        )

    store = RopSchedulerHealthStore(settings.rop_scheduler_health_path)
    try:
        runtime = store.read()
    except RuntimeError:
        return RopSchedulerHealthReport(
            status=RopSchedulerHealthStatus.UNAVAILABLE,
            scheduler_state=plan.state,
            reason="health_state_unreadable",
            poll_seconds=settings.rop_scheduler_poll_seconds,
            stale_after_seconds=stale_after_seconds,
            process_started_at=None,
            last_tick_completed_at=None,
            last_tick_age_seconds=None,
            last_tick_status=None,
            last_tick_due=0,
            last_tick_delivered=0,
            last_tick_failed=0,
            last_delivery_at=None,
            consecutive_failures=0,
        )

    if runtime is None:
        return RopSchedulerHealthReport(
            status=RopSchedulerHealthStatus.NOT_STARTED,
            scheduler_state=plan.state,
            reason="runtime_state_missing",
            poll_seconds=settings.rop_scheduler_poll_seconds,
            stale_after_seconds=stale_after_seconds,
            process_started_at=None,
            last_tick_completed_at=None,
            last_tick_age_seconds=None,
            last_tick_status=None,
            last_tick_due=0,
            last_tick_delivered=0,
            last_tick_failed=0,
            last_delivery_at=None,
            consecutive_failures=0,
        )

    if runtime.process_started_at is None:
        return RopSchedulerHealthReport(
            status=RopSchedulerHealthStatus.UNAVAILABLE,
            scheduler_state=plan.state,
            reason="process_started_at_missing",
            poll_seconds=settings.rop_scheduler_poll_seconds,
            stale_after_seconds=stale_after_seconds,
            process_started_at=None,
            last_tick_completed_at=runtime.last_tick_completed_at,
            last_tick_age_seconds=None,
            last_tick_status=runtime.last_tick_status,
            last_tick_due=runtime.last_tick_due,
            last_tick_delivered=runtime.last_tick_delivered,
            last_tick_failed=runtime.last_tick_failed,
            last_delivery_at=runtime.last_delivery_at,
            consecutive_failures=runtime.consecutive_failures,
        )

    if (
        runtime.last_tick_completed_at is None
        or runtime.last_tick_completed_at < runtime.process_started_at
    ):
        return RopSchedulerHealthReport(
            status=RopSchedulerHealthStatus.STARTING,
            scheduler_state=plan.state,
            reason="first_tick_not_completed",
            poll_seconds=settings.rop_scheduler_poll_seconds,
            stale_after_seconds=stale_after_seconds,
            process_started_at=runtime.process_started_at,
            last_tick_completed_at=runtime.last_tick_completed_at,
            last_tick_age_seconds=None,
            last_tick_status=runtime.last_tick_status,
            last_tick_due=runtime.last_tick_due,
            last_tick_delivered=runtime.last_tick_delivered,
            last_tick_failed=runtime.last_tick_failed,
            last_delivery_at=runtime.last_delivery_at,
            consecutive_failures=runtime.consecutive_failures,
        )

    reference = _as_utc(now or datetime.now(UTC))
    tick_age_seconds = max(
        0,
        int((reference - runtime.last_tick_completed_at).total_seconds()),
    )

    if tick_age_seconds > stale_after_seconds:
        health_status = RopSchedulerHealthStatus.STALE
        reason = "last_tick_stale"
    elif (
        runtime.last_tick_status
        in {
            RopSchedulerTickStatus.PARTIAL.value,
            RopSchedulerTickStatus.ERROR.value,
        }
        or runtime.last_tick_failed > 0
    ):
        health_status = RopSchedulerHealthStatus.DEGRADED
        reason = "last_tick_failed_or_partial"
    else:
        health_status = RopSchedulerHealthStatus.HEALTHY
        reason = "last_tick_fresh"

    return RopSchedulerHealthReport(
        status=health_status,
        scheduler_state=plan.state,
        reason=reason,
        poll_seconds=settings.rop_scheduler_poll_seconds,
        stale_after_seconds=stale_after_seconds,
        process_started_at=runtime.process_started_at,
        last_tick_completed_at=runtime.last_tick_completed_at,
        last_tick_age_seconds=tick_age_seconds,
        last_tick_status=runtime.last_tick_status,
        last_tick_due=runtime.last_tick_due,
        last_tick_delivered=runtime.last_tick_delivered,
        last_tick_failed=runtime.last_tick_failed,
        last_delivery_at=runtime.last_delivery_at,
        consecutive_failures=runtime.consecutive_failures,
    )


def _format_datetime(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "нет данных"


def format_rop_scheduler_health(report: RopSchedulerHealthReport) -> str:
    age = (
        f"{report.last_tick_age_seconds} сек"
        if report.last_tick_age_seconds is not None
        else "нет данных"
    )
    return "\n".join(
        [
            "ИИ-РОП · Scheduler Health",
            f"• health: {report.status.value}",
            f"• scheduler state: {report.scheduler_state.value}",
            f"• reason: {report.reason}",
            f"• poll interval: {report.poll_seconds} сек",
            f"• stale after: {report.stale_after_seconds} сек",
            f"• process started: {_format_datetime(report.process_started_at)}",
            f"• last tick: {_format_datetime(report.last_tick_completed_at)}",
            f"• last tick age: {age}",
            f"• last tick status: {report.last_tick_status or 'нет данных'}",
            f"• last tick due/delivered/failed: "
            f"{report.last_tick_due}/{report.last_tick_delivered}/{report.last_tick_failed}",
            f"• last delivery: {_format_datetime(report.last_delivery_at)}",
            f"• consecutive failures: {report.consecutive_failures}",
            "",
            "Это техническое здоровье scheduler, а не SLA бизнеса и не оценка менеджеров.",
        ]
    )
