from __future__ import annotations

from app.services.rop_policy_engine import (
    RuleState,
    Verdict,
    classify_sale_stage,
    conversion_readiness,
    first_response_readiness,
    load_policy_contract,
    policy_engine_readiness,
    proposal_readiness,
    qualifying_activity,
    stage_stale_readiness,
)


def contract():
    return load_policy_contract()


def test_410c_contract_loads() -> None:
    value = contract()

    assert value.binding["business_policy_funnel"]["category_id"] == 7


def test_410c_first_response_contract_ready() -> None:
    decision = first_response_readiness(contract())

    assert decision.state is RuleState.READY

    assert decision.verdict is Verdict.OK

    assert decision.threshold_seconds == 900


def test_410c_stage_threshold_ready_after_precedence_resolution() -> None:
    decision = stage_stale_readiness(
        contract(),
        "C7:PREPARATION",
    )

    assert decision.state is RuleState.READY

    assert decision.threshold_seconds == 108000


def test_410c_new_stage_alias_ready_after_confirmation() -> None:
    decision = stage_stale_readiness(
        contract(),
        "C7:NEW",
    )

    assert decision.state is RuleState.READY

    assert decision.threshold_seconds == 900


def test_410c_other_funnel_not_applicable() -> None:
    decision = stage_stale_readiness(
        contract(),
        "C8:NEW",
    )

    assert decision.state is RuleState.NOT_APPLICABLE

    assert decision.verdict is Verdict.NOT_APPLICABLE


def test_410c_proposal_ready_after_alias_confirmation() -> None:
    decision = proposal_readiness(contract())

    assert decision.state is RuleState.READY

    assert decision.threshold_seconds == 72000


def test_410c_conversion_ready_after_alias_confirmation() -> None:
    decision = conversion_readiness(contract())

    assert decision.state is RuleState.READY


def test_410c_won_classification_ready() -> None:
    decision = classify_sale_stage(
        contract(),
        "C7:WON",
    )

    assert decision.state is RuleState.READY

    assert decision.details["classification"] == "success"


def test_410c_lost_classification_ready() -> None:
    decision = classify_sale_stage(
        contract(),
        "C7:LOSE",
    )

    assert decision.state is RuleState.READY

    assert decision.details["classification"] == "lost"


def test_410c_activity_contract_ready() -> None:
    assert qualifying_activity(contract()) == (
        "outbound_call",
        "inbound_call",
        "message_to_client",
        "commercial_proposal_sent",
    )


def test_410c_readiness_snapshot_after_business_resolution() -> None:
    result = policy_engine_readiness(contract())

    assert result["first_response"].state is RuleState.READY

    assert result["stale_new"].state is RuleState.READY

    assert result["stale_needs_discovery"].state is RuleState.READY

    assert result["proposal"].state is RuleState.READY

    assert result["conversion"].state is RuleState.READY
