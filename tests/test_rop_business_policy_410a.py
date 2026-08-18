from __future__ import annotations

import json
from pathlib import Path

from app.services.rop_business_policy_registry import (
    ApprovalStatus,
    load_business_policy_document,
)


def policies():
    document = load_business_policy_document("config/rop-business-policies.json")

    assert document.valid is True

    return {item.key: item for item in document.policies}


def test_410a_first_response_business_values_preserved() -> None:
    first = policies()["first_response_sla"]

    assert first.approval_status is ApprovalStatus.APPROVED

    assert first.parameters["timer_start"] == "lead_created"

    assert first.parameters["threshold_seconds"] == 900

    assert first.parameters["clock"] == "business_hours_only"

    assert first.parameters["outside_working_hours"] == "start_at_next_workday"

    assert first.parameters["reassignment"] == "continue_from_original_start"


def test_410a_first_response_blockers_can_be_resolved_later() -> None:
    first = policies()["first_response_sla"]

    assert first.parameters["blocked_fields"] == []

    schedule = first.parameters["work_schedule"]

    assert schedule["timezone"] == "Europe/Moscow"

    assert schedule["working_weekdays"] == [1, 2, 3, 4, 5]

    assert schedule["workday_start"] == "09:00"

    assert schedule["workday_end"] == "19:00"


def test_410a_stale_values_preserved() -> None:
    stale = policies()["stale_deal"]

    assert stale.approval_status is ApprovalStatus.APPROVED

    assert stale.parameters["general_inactivity_threshold_seconds"] == 900

    stages = stale.parameters["stage_thresholds"]

    assert (
        stages["\u041d\u043e\u0432\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430"][
            "threshold_seconds"
        ]
        == 900
    )

    assert (
        stages[
            "\u0412\u044b\u044f\u0432\u043b\u0435\u043d\u0438\u0435 "
            "\u043f\u043e\u0442\u0440\u0435\u0431\u043d\u043e\u0441\u0442\u0435\u0439"
        ]["threshold_seconds"]
        == 108000
    )

    assert (
        stages[
            "\u041f\u043e\u0434\u0431\u043e\u0440 "
            "\u043f\u0430\u043a\u0435\u0442\u043d\u043e\u0433\u043e "
            "\u0442\u0443\u0440\u0430"
        ]["threshold_seconds"]
        == 14400
    )


def test_410a_proposal_rule_preserved() -> None:
    proposal = policies()["proposal_stale"]

    assert proposal.approval_status is ApprovalStatus.APPROVED

    assert proposal.parameters["attention_after_no_client_response_seconds"] == 72000


def test_410a_incomplete_scoring_rules_remain_pending() -> None:
    value = policies()

    assert value["manager_rating"].approval_status is ApprovalStatus.PENDING

    assert value["sales_plan_fact"].approval_status is ApprovalStatus.PENDING

    assert value["management_escalation"].approval_status is ApprovalStatus.PENDING


def test_410a_questionnaire_source_is_preserved() -> None:
    payload = json.loads(
        Path("config/rop-business-questionnaire-2026-08-18.json").read_text(encoding="utf-8")
    )

    assert payload["morning_report"]["desired_time"] == "10:00"

    assert payload["morning_report"]["selected_item_count"] == 13
