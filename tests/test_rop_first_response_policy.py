from app.config import Settings
from app.services.rop_first_response_policy import (
    FirstResponsePolicyState,
    build_first_response_policy,
    format_first_response_policy_for_ai,
)


def test_first_response_policy_is_disabled_by_default() -> None:
    settings = Settings(_env_file=None)
    policy = build_first_response_policy(settings)

    assert policy.state is FirstResponsePolicyState.DISABLED
    assert policy.blockers == ("policy_disabled",)


def test_enabled_incomplete_policy_is_blocked() -> None:
    settings = Settings(
        _env_file=None,
        rop_first_response_policy_enabled=True,
    )
    policy = build_first_response_policy(settings)

    assert policy.state is FirstResponsePolicyState.BLOCKED
    assert "timer_start_missing" in policy.blockers
    assert "response_event_missing" in policy.blockers
    assert "clock_missing" in policy.blockers
    assert "reassignment_mode_missing" in policy.blockers
    assert "threshold_seconds_missing_or_invalid" in policy.blockers


def test_supported_complete_policy_is_ready() -> None:
    settings = Settings(
        _env_file=None,
        rop_first_response_policy_enabled=True,
        rop_first_response_timer_start="lead_created",
        rop_first_response_event="manager_evidence",
        rop_first_response_clock="calendar_elapsed",
        rop_first_response_reassignment_mode="not_attributed",
        rop_first_response_threshold_seconds=900,
    )
    policy = build_first_response_policy(settings)

    assert policy.state is FirstResponsePolicyState.READY
    assert policy.blockers == ()
    assert policy.threshold_seconds == 900


def test_unimplemented_business_hours_is_blocked() -> None:
    settings = Settings(
        _env_file=None,
        rop_first_response_policy_enabled=True,
        rop_first_response_timer_start="lead_created",
        rop_first_response_event="confirmed_communication",
        rop_first_response_clock="business_hours",
        rop_first_response_reassignment_mode="not_attributed",
        rop_first_response_threshold_seconds=900,
    )
    policy = build_first_response_policy(settings)

    assert policy.state is FirstResponsePolicyState.BLOCKED
    assert "clock_unsupported:business_hours" in policy.blockers


def test_policy_output_never_claims_compliance() -> None:
    settings = Settings(_env_file=None)
    text = format_first_response_policy_for_ai(build_first_response_policy(settings))

    assert "state: disabled" in text
    assert "НЕ рассчитывает SLA compliance" in text
    assert "значения не подставляются автоматически" in text
