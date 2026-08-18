from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.services.rop_business_time import (
    TimerStatus,
    UnsupportedBusinessCalendarYear,
    evaluate_first_response,
    evaluate_stage_timer,
)
from app.services.rop_policy_engine import (
    RuleState,
    load_policy_contract,
    qualifying_activity,
    stage_stale_readiness,
)


class EvaluationState(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class EvaluationVerdict(StrEnum):
    OK = "ok"
    OPEN = "open"
    BREACH = "breach"
    ATTENTION = "attention"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_type: str
    source_id: str
    occurred_at: datetime
    event_kind: str
    actor_kind: str = ""
    actor_id: int | None = None


@dataclass(frozen=True, slots=True)
class FirstResponseCase:
    lead_id: int
    lead_created_at: datetime
    lead_created_evidence: EvidenceRef
    manager_response_at: datetime | None = None
    manager_response_evidence: EvidenceRef | None = None
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class StageTimerCase:
    deal_id: int
    stage_id: str
    stage_entered_at: datetime
    as_of: datetime
    stage_entry_evidence: EvidenceRef
    last_qualifying_activity_at: datetime | None = None
    last_activity_evidence: EvidenceRef | None = None
    last_activity_kind: str = ""


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    rule_key: str
    state: EvaluationState
    verdict: EvaluationVerdict
    entity_type: str
    entity_id: int
    stage_id: str = ""
    threshold_business_seconds: int | None = None
    elapsed_business_seconds: int | None = None
    anchor_at: datetime | None = None
    effective_start_at: datetime | None = None
    observed_at: datetime | None = None
    deadline_at: datetime | None = None
    reasons: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def _blocked(
    *,
    rule_key: str,
    entity_type: str,
    entity_id: int,
    stage_id: str = "",
    threshold_business_seconds: int | None = None,
    reasons: tuple[str, ...],
    evidence: tuple[EvidenceRef, ...] = (),
    details: dict[str, Any] | None = None,
) -> PolicyEvaluation:
    return PolicyEvaluation(
        rule_key=rule_key,
        state=EvaluationState.BLOCKED,
        verdict=EvaluationVerdict.BLOCKED,
        entity_type=entity_type,
        entity_id=entity_id,
        stage_id=stage_id,
        threshold_business_seconds=threshold_business_seconds,
        reasons=reasons,
        evidence=evidence,
        details=details or {},
    )


def _timer_verdict(
    status: TimerStatus,
) -> EvaluationVerdict:
    mapping = {
        TimerStatus.OK: EvaluationVerdict.OK,
        TimerStatus.OPEN: EvaluationVerdict.OPEN,
        TimerStatus.BREACH: EvaluationVerdict.BREACH,
        TimerStatus.ATTENTION: EvaluationVerdict.ATTENTION,
    }

    return mapping[status]


def evaluate_first_response_case(
    case: FirstResponseCase,
) -> PolicyEvaluation:
    if case.lead_id <= 0:
        return _blocked(
            rule_key="first_response_sla",
            entity_type="lead",
            entity_id=case.lead_id,
            reasons=("lead_id_invalid",),
        )

    evidence = [
        case.lead_created_evidence,
    ]

    if case.manager_response_at is not None and case.manager_response_evidence is None:
        return _blocked(
            rule_key="first_response_sla",
            entity_type="lead",
            entity_id=case.lead_id,
            reasons=("manager_response_evidence_missing",),
            evidence=tuple(evidence),
        )

    if case.manager_response_evidence is not None:
        response_evidence = case.manager_response_evidence

        evidence.append(response_evidence)

        if response_evidence.actor_kind != "directory_user":
            return _blocked(
                rule_key="first_response_sla",
                entity_type="lead",
                entity_id=case.lead_id,
                reasons=("manager_response_actor_not_directory_user",),
                evidence=tuple(evidence),
            )

        if response_evidence.actor_id is None:
            return _blocked(
                rule_key="first_response_sla",
                entity_type="lead",
                entity_id=case.lead_id,
                reasons=("manager_response_actor_id_missing",),
                evidence=tuple(evidence),
            )

    if case.manager_response_at is None and case.as_of is None:
        return _blocked(
            rule_key="first_response_sla",
            entity_type="lead",
            entity_id=case.lead_id,
            reasons=("response_or_as_of_required",),
            evidence=tuple(evidence),
        )

    try:
        timer = evaluate_first_response(
            lead_created_at=case.lead_created_at,
            response_at=case.manager_response_at,
            as_of=case.as_of,
        )
    except UnsupportedBusinessCalendarYear as exc:
        return _blocked(
            rule_key="first_response_sla",
            entity_type="lead",
            entity_id=case.lead_id,
            reasons=(str(exc),),
            evidence=tuple(evidence),
        )

    return PolicyEvaluation(
        rule_key="first_response_sla",
        state=EvaluationState.READY,
        verdict=_timer_verdict(timer.status),
        entity_type="lead",
        entity_id=case.lead_id,
        threshold_business_seconds=(timer.threshold_business_seconds),
        elapsed_business_seconds=(timer.elapsed_business_seconds),
        anchor_at=timer.anchor_at,
        effective_start_at=(timer.effective_start_at),
        observed_at=timer.observed_at,
        deadline_at=timer.deadline_at,
        evidence=tuple(evidence),
        details={
            "response_received": case.manager_response_at is not None,
        },
    )


def evaluate_stage_timer_case(
    case: StageTimerCase,
) -> PolicyEvaluation:
    if case.deal_id <= 0:
        return _blocked(
            rule_key="stale_deal",
            entity_type="deal",
            entity_id=case.deal_id,
            stage_id=case.stage_id,
            reasons=("deal_id_invalid",),
        )

    contract = load_policy_contract()

    readiness = stage_stale_readiness(
        contract,
        case.stage_id,
    )

    evidence = [
        case.stage_entry_evidence,
    ]

    if readiness.state is RuleState.NOT_APPLICABLE:
        return PolicyEvaluation(
            rule_key="stale_deal",
            state=(EvaluationState.NOT_APPLICABLE),
            verdict=(EvaluationVerdict.NOT_APPLICABLE),
            entity_type="deal",
            entity_id=case.deal_id,
            stage_id=case.stage_id,
            reasons=readiness.reasons,
            evidence=tuple(evidence),
            details=readiness.details,
        )

    if readiness.state is RuleState.BLOCKED:
        return _blocked(
            rule_key="stale_deal",
            entity_type="deal",
            entity_id=case.deal_id,
            stage_id=case.stage_id,
            threshold_business_seconds=(readiness.threshold_seconds),
            reasons=readiness.reasons,
            evidence=tuple(evidence),
            details=readiness.details,
        )

    if case.last_qualifying_activity_at is not None:
        if case.last_activity_evidence is None:
            return _blocked(
                rule_key="stale_deal",
                entity_type="deal",
                entity_id=case.deal_id,
                stage_id=case.stage_id,
                threshold_business_seconds=(readiness.threshold_seconds),
                reasons=("last_activity_evidence_missing",),
                evidence=tuple(evidence),
            )

        if not case.last_activity_kind:
            return _blocked(
                rule_key="stale_deal",
                entity_type="deal",
                entity_id=case.deal_id,
                stage_id=case.stage_id,
                threshold_business_seconds=(readiness.threshold_seconds),
                reasons=("last_activity_kind_missing",),
                evidence=tuple(evidence),
            )

        allowed_activity = set(qualifying_activity(contract))

        if case.last_activity_kind not in allowed_activity:
            return _blocked(
                rule_key="stale_deal",
                entity_type="deal",
                entity_id=case.deal_id,
                stage_id=case.stage_id,
                threshold_business_seconds=(readiness.threshold_seconds),
                reasons=("activity_kind_not_qualifying:" + case.last_activity_kind,),
                evidence=tuple(evidence),
            )

        evidence.append(case.last_activity_evidence)

    try:
        timer = evaluate_stage_timer(
            stage_id=case.stage_id,
            stage_entered_at=(case.stage_entered_at),
            as_of=case.as_of,
            last_qualifying_activity_at=(case.last_qualifying_activity_at),
        )
    except UnsupportedBusinessCalendarYear as exc:
        return _blocked(
            rule_key="stale_deal",
            entity_type="deal",
            entity_id=case.deal_id,
            stage_id=case.stage_id,
            threshold_business_seconds=(readiness.threshold_seconds),
            reasons=(str(exc),),
            evidence=tuple(evidence),
        )
    except ValueError as exc:
        return _blocked(
            rule_key="stale_deal",
            entity_type="deal",
            entity_id=case.deal_id,
            stage_id=case.stage_id,
            threshold_business_seconds=(readiness.threshold_seconds),
            reasons=(str(exc),),
            evidence=tuple(evidence),
        )

    return PolicyEvaluation(
        rule_key="stale_deal",
        state=EvaluationState.READY,
        verdict=_timer_verdict(timer.status),
        entity_type="deal",
        entity_id=case.deal_id,
        stage_id=case.stage_id,
        threshold_business_seconds=(timer.threshold_business_seconds),
        elapsed_business_seconds=(timer.elapsed_business_seconds),
        anchor_at=timer.anchor_at,
        effective_start_at=(timer.effective_start_at),
        observed_at=timer.observed_at,
        deadline_at=timer.deadline_at,
        evidence=tuple(evidence),
        details={
            "activity_reset_applied": case.last_qualifying_activity_at is not None,
            "last_activity_kind": case.last_activity_kind,
        },
    )


def evaluation_to_dict(
    value: PolicyEvaluation,
) -> dict[str, Any]:
    def iso(
        item: datetime | None,
    ) -> str | None:
        return item.isoformat() if item is not None else None

    return {
        "rule_key": value.rule_key,
        "state": value.state.value,
        "verdict": value.verdict.value,
        "entity_type": value.entity_type,
        "entity_id": value.entity_id,
        "stage_id": (value.stage_id or None),
        "threshold_business_seconds": value.threshold_business_seconds,
        "elapsed_business_seconds": value.elapsed_business_seconds,
        "anchor_at": iso(value.anchor_at),
        "effective_start_at": iso(value.effective_start_at),
        "observed_at": iso(value.observed_at),
        "deadline_at": iso(value.deadline_at),
        "reasons": list(value.reasons),
        "evidence": [
            {
                "source_type": item.source_type,
                "source_id": item.source_id,
                "occurred_at": item.occurred_at.isoformat(),
                "event_kind": item.event_kind,
                "actor_kind": item.actor_kind or None,
                "actor_id": item.actor_id,
            }
            for item in value.evidence
        ],
        "details": value.details,
    }


def format_policy_evaluation_for_ai(
    value: PolicyEvaluation,
) -> str:
    lines = [
        "ROP POLICY EVALUATION",
        ("entity=" + value.entity_type + ":" + str(value.entity_id)),
        "rule=" + value.rule_key,
        "state=" + value.state.value,
        "verdict=" + value.verdict.value,
    ]

    if value.stage_id:
        lines.append("stage_id=" + value.stage_id)

    if value.threshold_business_seconds is not None:
        lines.append("threshold_business_seconds=" + str(value.threshold_business_seconds))

    if value.elapsed_business_seconds is not None:
        lines.append("elapsed_business_seconds=" + str(value.elapsed_business_seconds))

    if value.deadline_at is not None:
        lines.append("deadline_at=" + value.deadline_at.isoformat())

    if value.reasons:
        lines.append("reasons=" + ",".join(value.reasons))

    if value.evidence:
        refs = [(item.source_type + ":" + item.source_id) for item in value.evidence]

        lines.append("evidence=" + ",".join(refs))

    lines.extend(
        [
            "",
            "GUARDRAILS:",
            ("- verdict is deterministic from supplied evidence;"),
            ("- no missing evidence is invented;"),
            ("- no customer message text is required;"),
            "- no CRM write is performed;",
        ]
    )

    return "\n".join(lines)
