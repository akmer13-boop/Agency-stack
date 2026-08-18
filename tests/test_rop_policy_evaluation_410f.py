from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.rop_policy_evaluation import (
    EvaluationState,
    EvaluationVerdict,
    EvidenceRef,
    FirstResponseCase,
    StageTimerCase,
    evaluate_first_response_case,
    evaluate_stage_timer_case,
    evaluation_to_dict,
    format_policy_evaluation_for_ai,
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


def lead_created() -> EvidenceRef:
    return EvidenceRef(
        source_type="crm_lead",
        source_id="100",
        occurred_at=dt(
            2026,
            8,
            18,
            10,
        ),
        event_kind="lead_created",
    )


def manager_response(
    minute: int,
) -> EvidenceRef:
    return EvidenceRef(
        source_type="openlines_message",
        source_id="501",
        occurred_at=dt(
            2026,
            8,
            18,
            10,
            minute,
        ),
        event_kind="manager_response",
        actor_kind="directory_user",
        actor_id=77,
    )


def stage_entry(
    stage_id: str,
    when: datetime,
) -> EvidenceRef:
    return EvidenceRef(
        source_type="crm_stage_history",
        source_id="stage-" + stage_id,
        occurred_at=when,
        event_kind="stage_entered",
    )


def test_410f_first_response_ok_with_evidence() -> None:
    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            manager_response_at=dt(
                2026,
                8,
                18,
                10,
                9,
            ),
            lead_created_evidence=(lead_created()),
            manager_response_evidence=(manager_response(9)),
        )
    )

    assert result.state is EvaluationState.READY

    assert result.verdict is EvaluationVerdict.OK

    assert result.elapsed_business_seconds == 540

    assert result.threshold_business_seconds == 900

    assert len(result.evidence) == 2


def test_410f_first_response_breach() -> None:
    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            manager_response_at=dt(
                2026,
                8,
                18,
                10,
                16,
            ),
            lead_created_evidence=(lead_created()),
            manager_response_evidence=(manager_response(16)),
        )
    )

    assert result.verdict is EvaluationVerdict.BREACH

    assert result.elapsed_business_seconds == 960


def test_410f_unanswered_first_response_open() -> None:
    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            as_of=dt(
                2026,
                8,
                18,
                10,
                14,
            ),
            lead_created_evidence=(lead_created()),
        )
    )

    assert result.verdict is EvaluationVerdict.OPEN


def test_410f_unanswered_first_response_breach() -> None:
    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            as_of=dt(
                2026,
                8,
                18,
                10,
                16,
            ),
            lead_created_evidence=(lead_created()),
        )
    )

    assert result.verdict is EvaluationVerdict.BREACH


def test_410f_manager_response_requires_evidence() -> None:
    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            manager_response_at=dt(
                2026,
                8,
                18,
                10,
                9,
            ),
            lead_created_evidence=(lead_created()),
        )
    )

    assert result.state is EvaluationState.BLOCKED

    assert "manager_response_evidence_missing" in result.reasons


def test_410f_first_response_requires_human_directory_actor() -> None:
    bad = EvidenceRef(
        source_type="openlines_message",
        source_id="bot-1",
        occurred_at=dt(
            2026,
            8,
            18,
            10,
            5,
        ),
        event_kind="manager_response",
        actor_kind="bot",
        actor_id=900,
    )

    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            manager_response_at=dt(
                2026,
                8,
                18,
                10,
                5,
            ),
            lead_created_evidence=(lead_created()),
            manager_response_evidence=bad,
        )
    )

    assert result.state is EvaluationState.BLOCKED

    assert "manager_response_actor_not_directory_user" in result.reasons


def test_410f_stage_timer_open_before_deadline() -> None:
    entered = dt(
        2026,
        8,
        17,
        10,
    )

    result = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=200,
            stage_id="C7:PREPARATION",
            stage_entered_at=entered,
            as_of=dt(
                2026,
                8,
                20,
                9,
                59,
            ),
            stage_entry_evidence=stage_entry(
                "C7:PREPARATION",
                entered,
            ),
        )
    )

    assert result.state is EvaluationState.READY

    assert result.verdict is EvaluationVerdict.OPEN

    assert result.threshold_business_seconds == 108000

    assert result.deadline_at == dt(
        2026,
        8,
        20,
        10,
    )


def test_410f_stage_timer_attention_at_deadline() -> None:
    entered = dt(
        2026,
        8,
        17,
        10,
    )

    result = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=200,
            stage_id="C7:PREPARATION",
            stage_entered_at=entered,
            as_of=dt(
                2026,
                8,
                20,
                10,
            ),
            stage_entry_evidence=stage_entry(
                "C7:PREPARATION",
                entered,
            ),
        )
    )

    assert result.verdict is EvaluationVerdict.ATTENTION


def test_410f_stage_activity_restarts_timer() -> None:
    entered = dt(
        2026,
        8,
        17,
        9,
    )

    activity_at = dt(
        2026,
        8,
        18,
        18,
    )

    result = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=200,
            stage_id="C7:PREPARATION",
            stage_entered_at=entered,
            as_of=dt(
                2026,
                8,
                21,
                17,
                59,
            ),
            stage_entry_evidence=stage_entry(
                "C7:PREPARATION",
                entered,
            ),
            last_qualifying_activity_at=(activity_at),
            last_activity_kind=("message_to_client"),
            last_activity_evidence=EvidenceRef(
                source_type="openlines_message",
                source_id="msg-700",
                occurred_at=activity_at,
                event_kind="message_to_client",
                actor_kind="directory_user",
                actor_id=77,
            ),
        )
    )

    assert result.verdict is EvaluationVerdict.OPEN

    assert result.anchor_at == activity_at

    assert result.deadline_at == dt(
        2026,
        8,
        21,
        18,
    )

    assert result.details["activity_reset_applied"] is True


def test_410f_nonqualifying_activity_is_blocked() -> None:
    entered = dt(
        2026,
        8,
        18,
        10,
    )

    activity_at = dt(
        2026,
        8,
        18,
        11,
    )

    result = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=200,
            stage_id="C7:PREPARATION",
            stage_entered_at=entered,
            as_of=dt(
                2026,
                8,
                18,
                12,
            ),
            stage_entry_evidence=stage_entry(
                "C7:PREPARATION",
                entered,
            ),
            last_qualifying_activity_at=(activity_at),
            last_activity_kind="crm_edit",
            last_activity_evidence=EvidenceRef(
                source_type="crm_activity",
                source_id="a-1",
                occurred_at=activity_at,
                event_kind="crm_edit",
            ),
        )
    )

    assert result.state is EvaluationState.BLOCKED

    assert any(reason.startswith("activity_kind_not_qualifying:") for reason in result.reasons)


def test_410f_other_funnel_is_not_applicable() -> None:
    entered = dt(
        2026,
        8,
        18,
        10,
    )

    result = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=200,
            stage_id="C8:NEW",
            stage_entered_at=entered,
            as_of=dt(
                2026,
                8,
                18,
                11,
            ),
            stage_entry_evidence=stage_entry(
                "C8:NEW",
                entered,
            ),
        )
    )

    assert result.state is EvaluationState.NOT_APPLICABLE

    assert result.verdict is EvaluationVerdict.NOT_APPLICABLE


def test_410f_potential_client_stays_blocked_until_field_binding() -> None:
    entered = dt(
        2026,
        8,
        18,
        10,
    )

    result = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=200,
            stage_id="C7:FINAL_INVOICE",
            stage_entered_at=entered,
            as_of=dt(
                2026,
                8,
                18,
                11,
            ),
            stage_entry_evidence=stage_entry(
                "C7:FINAL_INVOICE",
                entered,
            ),
        )
    )

    assert result.state is EvaluationState.BLOCKED

    assert "return_to_client_field_not_bound" in result.reasons


def test_410f_serialization_preserves_evidence_ids() -> None:
    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            manager_response_at=dt(
                2026,
                8,
                18,
                10,
                9,
            ),
            lead_created_evidence=(lead_created()),
            manager_response_evidence=(manager_response(9)),
        )
    )

    payload = evaluation_to_dict(result)

    assert payload["verdict"] == "ok"

    assert [item["source_id"] for item in payload["evidence"]] == [
        "100",
        "501",
    ]


def test_410f_ai_format_contains_verdict_and_evidence() -> None:
    result = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=100,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
            ),
            manager_response_at=dt(
                2026,
                8,
                18,
                10,
                9,
            ),
            lead_created_evidence=(lead_created()),
            manager_response_evidence=(manager_response(9)),
        )
    )

    text = format_policy_evaluation_for_ai(result)

    assert "verdict=ok" in text
    assert "crm_lead:100" in text
    assert "openlines_message:501" in text
    assert "no missing evidence is invented" in text
