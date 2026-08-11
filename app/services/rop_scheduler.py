from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings

_TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
_WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


class RopSchedulerState(StrEnum):
    DISABLED = "disabled"
    BLOCKED = "blocked"
    READY = "ready"


class RopSchedulerJobKind(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class RopSchedulerJob:
    name: str
    kind: RopSchedulerJobKind
    schedule_time: time
    weekday: int | None = None


@dataclass(frozen=True, slots=True)
class RopSchedulerPlan:
    state: RopSchedulerState
    timezone_name: str
    recipients: tuple[int, ...]
    jobs: tuple[RopSchedulerJob, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RopScheduledDelivery:
    job: RopSchedulerJob
    period_key: str
    recipient_id: int

    @property
    def ledger_key(self) -> str:
        return f"{self.job.name}|{self.period_key}|{self.recipient_id}"


def _parse_time(raw: str) -> time | None:
    value = raw.strip()
    if not _TIME_PATTERN.fullmatch(value):
        return None

    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        return time(hour=int(hour_text), minute=int(minute_text))
    except (TypeError, ValueError):
        return None


def _parse_weekday(raw: str) -> int | None:
    return _WEEKDAYS.get(raw.strip().lower())


def _timezone_valid(name: str) -> bool:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True


def build_rop_scheduler_plan(settings: Settings) -> RopSchedulerPlan:
    recipients = tuple(sorted(settings.rop_scheduler_recipients))

    if not settings.rop_scheduler_enabled:
        return RopSchedulerPlan(
            state=RopSchedulerState.DISABLED,
            timezone_name=settings.rop_timezone,
            recipients=recipients,
            jobs=(),
            blockers=("scheduler_disabled",),
        )

    blockers: list[str] = []
    jobs: list[RopSchedulerJob] = []

    if not _timezone_valid(settings.rop_timezone):
        blockers.append("timezone_invalid")

    if not recipients:
        blockers.append("recipient_ids_missing")

    permitted_recipients = (
        settings.admin_telegram_user_ids
        | settings.manager_telegram_user_ids
        | settings.observer_telegram_user_ids
    )
    for recipient_id in recipients:
        if recipient_id not in permitted_recipients:
            blockers.append(f"recipient_not_rop_role:{recipient_id}")

    if not settings.rop_scheduler_daily_enabled and not settings.rop_scheduler_weekly_enabled:
        blockers.append("no_jobs_enabled")

    if settings.rop_scheduler_daily_enabled:
        daily_time = _parse_time(settings.rop_scheduler_daily_time)
        if daily_time is None:
            blockers.append("daily_time_invalid_or_missing")
        else:
            jobs.append(
                RopSchedulerJob(
                    name="rop_daily",
                    kind=RopSchedulerJobKind.DAILY,
                    schedule_time=daily_time,
                )
            )

    if settings.rop_scheduler_weekly_enabled:
        weekly_day = _parse_weekday(settings.rop_scheduler_weekly_day)
        weekly_time = _parse_time(settings.rop_scheduler_weekly_time)

        if weekly_day is None:
            blockers.append("weekly_day_invalid_or_missing")
        if weekly_time is None:
            blockers.append("weekly_time_invalid_or_missing")

        if weekly_day is not None and weekly_time is not None:
            jobs.append(
                RopSchedulerJob(
                    name="rop_week",
                    kind=RopSchedulerJobKind.WEEKLY,
                    schedule_time=weekly_time,
                    weekday=weekly_day,
                )
            )

    state = RopSchedulerState.BLOCKED if blockers else RopSchedulerState.READY

    return RopSchedulerPlan(
        state=state,
        timezone_name=settings.rop_timezone,
        recipients=recipients,
        jobs=tuple(jobs),
        blockers=tuple(blockers),
    )


def _period_key(job: RopSchedulerJob, local_now: datetime) -> str:
    if job.kind is RopSchedulerJobKind.DAILY:
        return local_now.date().isoformat()

    iso = local_now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _job_is_due(job: RopSchedulerJob, local_now: datetime) -> bool:
    current_time = local_now.timetz().replace(tzinfo=None)

    if job.kind is RopSchedulerJobKind.DAILY:
        return current_time >= job.schedule_time

    if job.weekday is None:
        return False

    if local_now.weekday() > job.weekday:
        return True
    if local_now.weekday() < job.weekday:
        return False
    return current_time >= job.schedule_time


class RopSchedulerLedger:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Scheduler ledger is unreadable: {self.path}") from exc

        delivered = payload.get("delivered", {})
        if not isinstance(delivered, dict):
            raise RuntimeError(f"Scheduler ledger has invalid schema: {self.path}")

        return {str(key): str(value) for key, value in delivered.items()}

    def was_delivered(self, ledger_key: str) -> bool:
        return ledger_key in self._read()

    def mark_delivered(
        self,
        ledger_key: str,
        *,
        delivered_at: datetime,
    ) -> None:
        delivered = self._read()
        delivered[ledger_key] = delivered_at.astimezone(UTC).isoformat()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "delivered": delivered}

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


def due_rop_scheduler_deliveries(
    plan: RopSchedulerPlan,
    ledger: RopSchedulerLedger,
    *,
    now: datetime,
) -> tuple[RopScheduledDelivery, ...]:
    if plan.state is not RopSchedulerState.READY:
        return ()

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    local_now = now.astimezone(ZoneInfo(plan.timezone_name))
    deliveries: list[RopScheduledDelivery] = []

    for job in plan.jobs:
        if not _job_is_due(job, local_now):
            continue

        period_key = _period_key(job, local_now)
        for recipient_id in plan.recipients:
            delivery = RopScheduledDelivery(
                job=job,
                period_key=period_key,
                recipient_id=recipient_id,
            )
            if not ledger.was_delivered(delivery.ledger_key):
                deliveries.append(delivery)

    return tuple(deliveries)


def format_rop_scheduler_plan(plan: RopSchedulerPlan) -> str:
    lines = [
        "ИИ-РОП · Scheduler Status",
        f"• state: {plan.state.value}",
        f"• timezone: {plan.timezone_name}",
        f"• recipients configured: {len(plan.recipients)}",
    ]

    if plan.jobs:
        lines.append("• jobs:")
        for job in plan.jobs:
            schedule = job.schedule_time.strftime("%H:%M")
            if job.kind is RopSchedulerJobKind.WEEKLY:
                weekday = next(key for key, value in _WEEKDAYS.items() if value == job.weekday)
                schedule = f"{weekday} {schedule}"
            lines.append(f"  - {job.name}: {schedule}")
    else:
        lines.append("• jobs: none")

    if plan.blockers:
        lines.append("• blockers: " + ", ".join(plan.blockers))
    else:
        lines.append("• blockers: none")

    lines.extend(
        [
            "",
            "Безопасность:",
            "• scheduler отправляет только уже детерминированные read-only отчёты;",
            "• CRM write не используется;",
            "• время и получатели не подставляются автоматически;",
            "• default state = DISABLED;",
            "• delivery ledger хранится локально вне Git и защищает от повторной "
            "отправки одного job/period/recipient после рестарта.",
        ]
    )

    return "\n".join(lines)
