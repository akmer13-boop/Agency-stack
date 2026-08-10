from types import SimpleNamespace

from app.services.rop_activity_risk import (
    build_activity_aware_risk,
    format_activity_aware_risk,
    format_activity_aware_risk_compact,
    format_activity_aware_risk_for_ai,
)
from app.services.rop_deal_evidence import DealStageEvidence, _activity_type_label


def _evidence(
    *,
    communications: int | None,
    days_since_last_communication: int | None,
    next_open_activity_exists: bool,
) -> DealStageEvidence:
    return DealStageEvidence(
        deal_id="7040",
        stage_id="C8:PREPAYMENT_INVOICE",
        stage_entered_at=None,
        activities_after_stage=42,
        completed_after_stage=42,
        completed_communications_after_stage=communications,
        activity_type_counts=(("E-mail", 41), ("Другой тип (ID 6)", 1)),
        last_activity_type="E-mail",
        last_activity_at=None,
        last_activity_completed=True,
        days_since_last_activity=6,
        last_communication_type="E-mail" if communications else None,
        last_communication_at=None,
        days_since_last_communication=days_since_last_communication,
        next_open_activity_exists=next_open_activity_exists,
    )


def test_activity_aware_risk_separates_stage_work_history_and_next_action() -> None:
    report = SimpleNamespace(
        deal_id="7040",
        sla_severity="critical",
        stage_age_days=89,
        sla_rule_label="Follow-up после КП",
    )
    evidence = _evidence(
        communications=41,
        days_since_last_communication=6,
        next_open_activity_exists=False,
    )

    risk = build_activity_aware_risk(report, evidence)

    assert risk.stage_risk == "critical"
    assert risk.communication_evidence == "confirmed"
    assert risk.communications_after_stage == 41
    assert risk.days_since_last_communication == 6
    assert risk.next_action_state == "missing"

    text = format_activity_aware_risk(risk)
    assert "Stage risk: КРИТИЧНО" in text
    assert "завершённых коммуникаций 41" in text
    assert "последняя E-mail · 6 дн. назад" in text
    assert "нет оснований утверждать, что follow-up вообще не выполнялся" in text
    assert "следующего незавершённого действия" in text
    assert "не окрашиваются в SLA-критичность" in text

    compact = format_activity_aware_risk_compact(risk)
    assert "🔴 стадия 89 дн." in compact
    assert "🟢 коммуникации 41, последняя 6 дн. назад" in compact
    assert "🔴 next action отсутствует" in compact

    ai_text = format_activity_aware_risk_for_ai(risk)
    assert "Communication evidence: confirmed" in ai_text
    assert "Days since last completed communication: 6" in ai_text
    assert "нельзя утверждать 'follow-up не выполнялся'" in ai_text
    assert "не отдельным SLA" in ai_text


def test_activity_aware_risk_does_not_invent_reason_when_no_communication_is_recorded() -> None:
    report = SimpleNamespace(
        deal_id="7040",
        sla_severity="critical",
        stage_age_days=89,
        sla_rule_label="Follow-up после КП",
    )
    evidence = _evidence(
        communications=0,
        days_since_last_communication=None,
        next_open_activity_exists=True,
    )

    risk = build_activity_aware_risk(report, evidence)
    text = format_activity_aware_risk(risk)

    assert risk.communication_evidence == "none_recorded"
    assert risk.next_action_state == "present"
    assert "это сигнал отсутствия данных, а не установленная причина" in text
    assert "Причина, почему клиент или сделка не двигается дальше" in text


def test_unknown_bitrix_activity_type_is_not_given_an_invented_business_name() -> None:
    assert _activity_type_label("6") == "Другой тип (ID 6)"
