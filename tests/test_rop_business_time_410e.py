from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.rop_business_time import (
    TimerStatus,
    UnsupportedBusinessCalendarYear,
    add_business_seconds,
    business_day_seconds,
    business_seconds_between,
    evaluate_first_response,
    evaluate_stage_timer,
    is_business_day,
    ru_non_working_days,
    stage_threshold_business_seconds,
)

ZONE = ZoneInfo("Europe/Moscow")


def dt(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=ZONE,
    )


def test_410e_workday_is_ten_hours() -> None:
    assert business_day_seconds() == 36000


def test_410e_official_2026_holidays_loaded() -> None:
    holidays = ru_non_working_days(2026)

    assert (
        date(
            2026,
            1,
            9,
        )
        in holidays
    )

    assert (
        date(
            2026,
            2,
            23,
        )
        in holidays
    )

    assert (
        date(
            2026,
            3,
            9,
        )
        in holidays
    )

    assert (
        date(
            2026,
            5,
            1,
        )
        in holidays
    )

    assert (
        date(
            2026,
            6,
            12,
        )
        in holidays
    )

    assert (
        date(
            2026,
            11,
            4,
        )
        in holidays
    )

    assert (
        date(
            2026,
            12,
            31,
        )
        in holidays
    )


def test_410e_unsupported_year_fails_closed() -> None:
    with pytest.raises(UnsupportedBusinessCalendarYear):
        is_business_day(
            date(
                2027,
                1,
                11,
            )
        )


def test_410e_friday_1855_plus_15_minutes() -> None:
    result = add_business_seconds(
        dt(
            2026,
            8,
            14,
            18,
            55,
        ),
        900,
    )

    assert result == dt(
        2026,
        8,
        17,
        9,
        10,
    )


def test_410e_holiday_weekend_is_skipped() -> None:
    result = add_business_seconds(
        dt(
            2026,
            2,
            20,
            18,
            55,
        ),
        900,
    )

    assert result == dt(
        2026,
        2,
        24,
        9,
        10,
    )


def test_410e_out_of_hours_first_response_starts_next_workday() -> None:
    result = evaluate_first_response(
        lead_created_at=dt(
            2026,
            8,
            14,
            20,
        ),
        response_at=dt(
            2026,
            8,
            17,
            9,
            15,
        ),
    )

    assert result.effective_start_at == dt(
        2026,
        8,
        17,
        9,
    )

    assert result.deadline_at == dt(
        2026,
        8,
        17,
        9,
        15,
    )

    assert result.status is TimerStatus.OK


def test_410e_first_response_after_limit_is_breach() -> None:
    result = evaluate_first_response(
        lead_created_at=dt(
            2026,
            8,
            18,
            10,
        ),
        response_at=dt(
            2026,
            8,
            18,
            10,
            16,
        ),
    )

    assert result.status is TimerStatus.BREACH


def test_410e_nonworking_time_not_counted() -> None:
    seconds = business_seconds_between(
        dt(
            2026,
            8,
            14,
            18,
        ),
        dt(
            2026,
            8,
            17,
            10,
        ),
    )

    assert seconds == 7200


def test_410e_stage_thresholds_use_business_units() -> None:
    assert stage_threshold_business_seconds("C7:NEW") == 900

    assert stage_threshold_business_seconds("C7:PREPARATION") == 108000

    assert stage_threshold_business_seconds("C7:PREPAYMENT_INVOICE") == 14400

    assert stage_threshold_business_seconds("C7:UC_IAVLST") == 86400

    assert stage_threshold_business_seconds("C7:EXECUTING") == 72000


def test_410e_three_business_days_deadline() -> None:
    result = evaluate_stage_timer(
        stage_id="C7:PREPARATION",
        stage_entered_at=dt(
            2026,
            8,
            17,
            10,
        ),
        as_of=dt(
            2026,
            8,
            20,
            9,
            59,
        ),
    )

    assert result.deadline_at == dt(
        2026,
        8,
        20,
        10,
    )

    assert result.status is TimerStatus.OPEN


def test_410e_stage_activity_restarts_timer() -> None:
    result = evaluate_stage_timer(
        stage_id="C7:PREPARATION",
        stage_entered_at=dt(
            2026,
            8,
            17,
            9,
        ),
        last_qualifying_activity_at=dt(
            2026,
            8,
            18,
            18,
        ),
        as_of=dt(
            2026,
            8,
            21,
            17,
            59,
        ),
    )

    assert result.deadline_at == dt(
        2026,
        8,
        21,
        18,
    )

    assert result.status is TimerStatus.OPEN


def test_410e_two_business_day_proposal_deadline() -> None:
    result = evaluate_stage_timer(
        stage_id="C7:EXECUTING",
        stage_entered_at=dt(
            2026,
            8,
            14,
            18,
        ),
        as_of=dt(
            2026,
            8,
            18,
            18,
        ),
    )

    assert result.deadline_at == dt(
        2026,
        8,
        18,
        18,
    )

    assert result.status is TimerStatus.ATTENTION
