from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class BusinessCalendar:
    timezone: str
    working_weekdays: frozenset[int]
    workday_start: time
    workday_end: time
    holidays: frozenset[date]


def default_business_calendar(
    *,
    holidays: frozenset[date] | None = None,
) -> BusinessCalendar:
    return BusinessCalendar(
        timezone="Europe/Moscow",
        working_weekdays=frozenset({1, 2, 3, 4, 5}),
        workday_start=time(9, 0),
        workday_end=time(19, 0),
        holidays=(holidays if holidays is not None else frozenset()),
    )


def localize(
    value: datetime,
    calendar: BusinessCalendar,
) -> datetime:
    zone = ZoneInfo(calendar.timezone)

    if value.tzinfo is None:
        return value.replace(tzinfo=zone)

    return value.astimezone(zone)


def is_business_day(
    value: date,
    calendar: BusinessCalendar,
) -> bool:
    if value.isoweekday() not in calendar.working_weekdays:
        return False

    return value not in calendar.holidays


def is_business_time(
    value: datetime,
    calendar: BusinessCalendar,
) -> bool:
    local = localize(
        value,
        calendar,
    )

    if not is_business_day(
        local.date(),
        calendar,
    ):
        return False

    current = local.time().replace(tzinfo=None)

    return calendar.workday_start <= current < calendar.workday_end
