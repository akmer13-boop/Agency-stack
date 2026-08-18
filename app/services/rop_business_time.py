from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.rop_policy_engine import (
    RuleState,
    load_policy_contract,
    stage_stale_readiness,
)

CALENDAR_PATH = Path("config/rop-business-calendar.json")

HOLIDAY_PATTERN = "config/rop-ru-holidays-{year}.json"


class UnsupportedBusinessCalendarYear(ValueError):
    pass


class TimerStatus(StrEnum):
    OPEN = "open"
    OK = "ok"
    BREACH = "breach"
    ATTENTION = "attention"


@dataclass(frozen=True, slots=True)
class CalendarSpec:
    timezone: str
    working_weekdays: frozenset[int]
    workday_start: time
    workday_end: time


@dataclass(frozen=True, slots=True)
class TimerEvaluation:
    status: TimerStatus
    anchor_at: datetime
    effective_start_at: datetime
    observed_at: datetime
    deadline_at: datetime
    threshold_business_seconds: int
    elapsed_business_seconds: int


@lru_cache
def load_calendar_spec() -> CalendarSpec:
    payload = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))

    start_h, start_m = (int(item) for item in payload["workday_start"].split(":"))

    end_h, end_m = (int(item) for item in payload["workday_end"].split(":"))

    return CalendarSpec(
        timezone=payload["timezone"],
        working_weekdays=frozenset(int(item) for item in payload["working_weekdays"]),
        workday_start=time(
            start_h,
            start_m,
        ),
        workday_end=time(
            end_h,
            end_m,
        ),
    )


@lru_cache
def ru_non_working_days(
    year: int,
) -> frozenset[date]:
    path = Path(HOLIDAY_PATTERN.format(year=year))

    if not path.exists():
        raise UnsupportedBusinessCalendarYear("unsupported_ru_business_calendar_year:" + str(year))

    payload = json.loads(path.read_text(encoding="utf-8"))

    if payload.get("year") != year:
        raise ValueError("holiday_calendar_year_mismatch")

    return frozenset(date.fromisoformat(item) for item in payload["non_working_dates"])


def _local(
    value: datetime,
    spec: CalendarSpec,
) -> datetime:
    zone = ZoneInfo(spec.timezone)

    if value.tzinfo is None:
        return value.replace(tzinfo=zone)

    return value.astimezone(zone)


def _window(
    day: date,
    spec: CalendarSpec,
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(spec.timezone)

    start = datetime.combine(
        day,
        spec.workday_start,
        tzinfo=zone,
    )

    end = datetime.combine(
        day,
        spec.workday_end,
        tzinfo=zone,
    )

    return start, end


def is_business_day(
    day: date,
    spec: CalendarSpec | None = None,
) -> bool:
    current = spec if spec is not None else load_calendar_spec()

    holidays = ru_non_working_days(day.year)

    return day.isoweekday() in current.working_weekdays and day not in holidays


def next_business_instant(
    value: datetime,
    spec: CalendarSpec | None = None,
) -> datetime:
    current = spec if spec is not None else load_calendar_spec()

    local = _local(
        value,
        current,
    )

    day = local.date()

    for _ in range(370):
        if is_business_day(
            day,
            current,
        ):
            start, end = _window(
                day,
                current,
            )

            if day == local.date():
                if local < start:
                    return start

                if local < end:
                    return local
            else:
                return start

        day += timedelta(days=1)

    raise RuntimeError("business_day_not_found")


def business_seconds_between(
    start: datetime,
    end: datetime,
    spec: CalendarSpec | None = None,
) -> int:
    current = spec if spec is not None else load_calendar_spec()

    left = _local(
        start,
        current,
    )

    right = _local(
        end,
        current,
    )

    if right < left:
        raise ValueError("end_before_start")

    total = 0
    day = left.date()

    while day <= right.date():
        if is_business_day(
            day,
            current,
        ):
            window_start, window_end = _window(
                day,
                current,
            )

            overlap_start = max(
                left,
                window_start,
            )

            overlap_end = min(
                right,
                window_end,
            )

            if overlap_end > overlap_start:
                total += int((overlap_end - overlap_start).total_seconds())

        day += timedelta(days=1)

    return total


def add_business_seconds(
    start: datetime,
    seconds: int,
    spec: CalendarSpec | None = None,
) -> datetime:
    if seconds < 0:
        raise ValueError("negative_business_seconds")

    current = spec if spec is not None else load_calendar_spec()

    cursor = next_business_instant(
        start,
        current,
    )

    if seconds == 0:
        return cursor

    remaining = seconds

    while True:
        _, day_end = _window(
            cursor.date(),
            current,
        )

        available = int((day_end - cursor).total_seconds())

        if remaining <= available:
            return cursor + timedelta(seconds=remaining)

        remaining -= available

        cursor = next_business_instant(
            day_end + timedelta(microseconds=1),
            current,
        )


def business_day_seconds(
    spec: CalendarSpec | None = None,
) -> int:
    current = spec if spec is not None else load_calendar_spec()

    today = date(
        2026,
        8,
        18,
    )

    start, end = _window(
        today,
        current,
    )

    return int((end - start).total_seconds())


def stage_anchor(
    *,
    stage_entered_at: datetime,
    last_qualifying_activity_at: datetime | None,
) -> datetime:
    if last_qualifying_activity_at is None:
        return stage_entered_at

    entered = _local(
        stage_entered_at,
        load_calendar_spec(),
    )

    activity = _local(
        last_qualifying_activity_at,
        load_calendar_spec(),
    )

    return max(
        entered,
        activity,
    )


def stage_threshold_business_seconds(
    stage_id: str,
) -> int:
    contract = load_policy_contract()

    decision = stage_stale_readiness(
        contract,
        stage_id,
    )

    if decision.state is not RuleState.READY:
        reasons = ",".join(decision.reasons)

        raise ValueError("stage_timer_not_ready:" + reasons)

    if decision.threshold_seconds is None:
        raise ValueError("stage_threshold_missing")

    return decision.threshold_seconds


def evaluate_first_response(
    *,
    lead_created_at: datetime,
    response_at: datetime | None = None,
    as_of: datetime | None = None,
) -> TimerEvaluation:
    contract = load_policy_contract()

    threshold = contract.policies["first_response_sla"]["parameters"]["threshold_seconds"]

    observed = response_at if response_at is not None else as_of

    if observed is None:
        raise ValueError("response_or_as_of_required")

    effective_start = next_business_instant(lead_created_at)

    deadline = add_business_seconds(
        lead_created_at,
        threshold,
    )

    elapsed = business_seconds_between(
        lead_created_at,
        observed,
    )

    if response_at is not None:
        status = TimerStatus.OK if elapsed <= threshold else TimerStatus.BREACH
    else:
        status = TimerStatus.BREACH if elapsed > threshold else TimerStatus.OPEN

    return TimerEvaluation(
        status=status,
        anchor_at=lead_created_at,
        effective_start_at=effective_start,
        observed_at=observed,
        deadline_at=deadline,
        threshold_business_seconds=threshold,
        elapsed_business_seconds=elapsed,
    )


def evaluate_stage_timer(
    *,
    stage_id: str,
    stage_entered_at: datetime,
    as_of: datetime,
    last_qualifying_activity_at: datetime | None = None,
) -> TimerEvaluation:
    threshold = stage_threshold_business_seconds(stage_id)

    anchor = stage_anchor(
        stage_entered_at=stage_entered_at,
        last_qualifying_activity_at=last_qualifying_activity_at,
    )

    effective_start = next_business_instant(anchor)

    deadline = add_business_seconds(
        anchor,
        threshold,
    )

    elapsed = business_seconds_between(
        anchor,
        as_of,
    )

    status = TimerStatus.ATTENTION if elapsed >= threshold else TimerStatus.OPEN

    return TimerEvaluation(
        status=status,
        anchor_at=anchor,
        effective_start_at=effective_start,
        observed_at=as_of,
        deadline_at=deadline,
        threshold_business_seconds=threshold,
        elapsed_business_seconds=elapsed,
    )
