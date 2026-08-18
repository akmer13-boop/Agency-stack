from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.rop_business_calendar import (
    default_business_calendar,
    is_business_day,
    is_business_time,
)
from app.services.rop_policy_engine import (
    RuleState,
    conversion_readiness,
    first_response_readiness,
    load_policy_contract,
    proposal_readiness,
    stage_stale_readiness,
)


def contract():
    return load_policy_contract()


def test_410d_calendar_config() -> None:
    payload = json.loads(Path("config/rop-business-calendar.json").read_text(encoding="utf-8"))

    assert payload["timezone"] == "Europe/Moscow"

    assert payload["working_weekdays"] == [1, 2, 3, 4, 5]

    assert payload["workday_start"] == "09:00"

    assert payload["workday_end"] == "19:00"


def test_410d_weekday_business_time() -> None:
    calendar = default_business_calendar()

    moment = datetime(
        2026,
        8,
        18,
        10,
        0,
        tzinfo=ZoneInfo("Europe/Moscow"),
    )

    assert is_business_time(
        moment,
        calendar,
    )


def test_410d_weekend_closed() -> None:
    calendar = default_business_calendar()

    assert not is_business_day(
        date(2026, 8, 22),
        calendar,
    )


def test_410d_injected_holiday_closed() -> None:
    holiday = date(
        2026,
        8,
        18,
    )

    calendar = default_business_calendar(holidays=frozenset({holiday}))

    assert not is_business_day(
        holiday,
        calendar,
    )


def test_410d_first_response_readiness_ready() -> None:
    decision = first_response_readiness(contract())

    assert decision.state is RuleState.READY


def test_410d_proposal_ready() -> None:
    decision = proposal_readiness(contract())

    assert decision.state is RuleState.READY


def test_410d_conversion_ready() -> None:
    decision = conversion_readiness(contract())

    assert decision.state is RuleState.READY


def test_410d_new_stage_ready() -> None:
    decision = stage_stale_readiness(
        contract(),
        "C7:NEW",
    )

    assert decision.state is RuleState.READY


def test_410d_stage_timer_semantics() -> None:
    payload = json.loads(Path("config/rop-business-policies.json").read_text(encoding="utf-8"))

    stale = payload["policies"]["stale_deal"]["parameters"]

    timer = stale["timer_semantics"]

    assert timer["start_on_stage_entry"] is True

    assert timer["restart_on_qualifying_activity"] is True

    assert timer["stop_on_stage_exit"] is True

    assert stale["threshold_application"] == "explicit_stage_threshold"

    assert stale["unspecified_stage_mode"] == "not_applicable"
