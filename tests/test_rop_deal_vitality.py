from decimal import Decimal
from types import SimpleNamespace

from app.services.rop_activity_risk import ActivityAwareDealRisk
from app.services.rop_deal_vitality import (
    build_deal_vitality,
    format_deal_vitality,
    format_deal_vitality_for_ai,
)


def _report() -> SimpleNamespace:
    return SimpleNamespace(
        deal_id="7040",
        opportunity=Decimal("6000000"),
        currency="RUB",
        stage_age_days=89,
    )


def test_vitality_requires_confirmation_for_critical_stage_without_next_action() -> None:
    risk = ActivityAwareDealRisk(
        deal_id="7040",
        stage_risk="critical",
        stage_age_days=89,
        stage_rule_label="Follow-up после КП",
        communication_evidence="confirmed",
        communications_after_stage=41,
        last_communication_type="E-mail",
        days_since_last_communication=6,
        next_action_state="missing",
    )

    vitality = build_deal_vitality(_report(), risk)

    assert vitality.state == "needs_confirmation"
    assert vitality.pipeline_confidence == "unconfirmed"
    text = format_deal_vitality(vitality)
    assert "актуальность требует подтверждения" in text
    assert "неподтверждённым pipeline" in text
    assert "сначала подтвердить, жива ли сделка" in text
    assert "не подтверждает актуальное намерение клиента" in text

    ai_text = format_deal_vitality_for_ai(vitality)
    assert "Pipeline confidence: unconfirmed" in ai_text
    assert "first recommend verifying actual deal status" in ai_text
    assert "never auto-close" in ai_text


def test_vitality_marks_closure_check_candidate_without_comms_or_next_action() -> None:
    risk = ActivityAwareDealRisk(
        deal_id="7040",
        stage_risk="critical",
        stage_age_days=89,
        stage_rule_label="Follow-up после КП",
        communication_evidence="none_recorded",
        communications_after_stage=0,
        last_communication_type=None,
        days_since_last_communication=None,
        next_action_state="missing",
    )

    vitality = build_deal_vitality(_report(), risk)

    assert vitality.state == "closure_check_candidate"
    assert vitality.pipeline_confidence == "unconfirmed"
    text = format_deal_vitality(vitality)
    assert "кандидат на проверку закрытия" in text
    assert "автоматически закрывать карточку нельзя" in text


def test_vitality_only_says_working_signals_when_comms_and_next_action_exist() -> None:
    risk = ActivityAwareDealRisk(
        deal_id="7040",
        stage_risk="attention",
        stage_age_days=10,
        stage_rule_label="Follow-up после КП",
        communication_evidence="confirmed",
        communications_after_stage=3,
        last_communication_type="Звонок",
        days_since_last_communication=1,
        next_action_state="present",
    )

    vitality = build_deal_vitality(_report(), risk)

    assert vitality.state == "managed_signals"
    assert vitality.pipeline_confidence == "working_signals"
    text = format_deal_vitality(vitality)
    assert "есть признаки текущего ведения" in text
    assert "не доказывает намерение клиента купить" in text
