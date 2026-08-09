from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.services.rop_activity_risk import ActivityAwareDealRisk
from app.services.rop_deal import DealDrilldown

DealVitalityState = Literal[
    "managed_signals",
    "needs_confirmation",
    "closure_check_candidate",
    "insufficient_data",
]
PipelineConfidence = Literal["working_signals", "unconfirmed", "unknown"]


@dataclass(frozen=True, slots=True)
class DealVitality:
    deal_id: str
    state: DealVitalityState
    pipeline_confidence: PipelineConfidence
    opportunity: Decimal
    currency: str
    stage_age_days: int | None
    communications_after_stage: int | None
    days_since_last_communication: int | None
    next_action_present: bool
    reasons: tuple[str, ...]


def build_deal_vitality(
    report: DealDrilldown,
    risk: ActivityAwareDealRisk,
) -> DealVitality:
    next_action_present = risk.next_action_state == "present"
    reasons: list[str] = []

    if (
        risk.stage_risk == "critical"
        and not next_action_present
        and risk.communication_evidence == "none_recorded"
    ):
        state: DealVitalityState = "closure_check_candidate"
        confidence: PipelineConfidence = "unconfirmed"
        reasons.extend(
            [
                "stage-specific SLA критичен",
                "следующий незавершённый шаг отсутствует",
                "завершённые коммуникации после входа на стадию не найдены",
            ]
        )
    elif risk.stage_risk in {"critical", "attention"} and not next_action_present:
        state = "needs_confirmation"
        confidence = "unconfirmed"
        reasons.extend(
            [
                "карточка находится в измеряемой SLA-зоне",
                "следующий незавершённый шаг отсутствует",
            ]
        )
        if risk.communication_evidence == "confirmed":
            reasons.append(
                "история коммуникаций есть, но она не подтверждает актуальное намерение клиента"
            )
    elif risk.communication_evidence == "confirmed" and next_action_present:
        state = "managed_signals"
        confidence = "working_signals"
        reasons.extend(
            [
                "после входа на стадию подтверждены коммуникации",
                "в CRM есть следующий незавершённый шаг",
            ]
        )
    else:
        state = "insufficient_data"
        confidence = "unknown"
        reasons.append(
            "имеющихся агрегатов недостаточно, чтобы подтвердить текущую актуальность сделки"
        )

    return DealVitality(
        deal_id=report.deal_id,
        state=state,
        pipeline_confidence=confidence,
        opportunity=report.opportunity,
        currency=report.currency,
        stage_age_days=report.stage_age_days,
        communications_after_stage=risk.communications_after_stage,
        days_since_last_communication=risk.days_since_last_communication,
        next_action_present=next_action_present,
        reasons=tuple(reasons),
    )


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def vitality_label(vitality: DealVitality) -> str:
    if vitality.state == "managed_signals":
        return "🟢 есть признаки текущего ведения"
    if vitality.state == "needs_confirmation":
        return "⚠️ актуальность требует подтверждения"
    if vitality.state == "closure_check_candidate":
        return "🟠 кандидат на проверку закрытия"
    return "⚪ актуальность не установлена"


def format_deal_vitality(vitality: DealVitality) -> str:
    lines = [
        "ИИ-РОП · deal vitality",
        f"• {vitality_label(vitality)}",
        "• CRM OPPORTUNITY: "
        f"{_money(vitality.opportunity)} {vitality.currency}",
    ]
    lines.extend(f"• Причина: {reason}." for reason in vitality.reasons)

    if vitality.pipeline_confidence == "unconfirmed":
        lines.append(
            "• Pipeline status: сумма остаётся фактом CRM, но до ручного подтверждения "
            "актуальности считается неподтверждённым pipeline для управленческого решения."
        )
    elif vitality.pipeline_confidence == "working_signals":
        lines.append(
            "• Pipeline status: есть признаки текущего ведения, но это не доказывает "
            "намерение клиента купить или вероятность закрытия."
        )
    else:
        lines.append(
            "• Pipeline status: данных недостаточно для подтверждения рабочего pipeline."
        )

    if vitality.state == "closure_check_candidate":
        lines.append(
            "• Первое действие РОПа: проверить фактический статус и необходимость закрытия; "
            "автоматически закрывать карточку нельзя."
        )
    elif vitality.state == "needs_confirmation":
        lines.append(
            "• Первое действие РОПа: сначала подтвердить, жива ли сделка. Если жива — "
            "зафиксировать следующий шаг; если неактуальна — закрыть по процессу компании."
        )
    elif vitality.state == "managed_signals":
        lines.append(
            "• Первое действие РОПа: проверить результат последней коммуникации и "
            "соответствие текущей стадии реальному статусу."
        )
    else:
        lines.append("• Первое действие РОПа: вручную подтвердить актуальный статус сделки.")

    lines.append(
        "\nDeal vitality — не вероятность продажи и не автоматическое решение о закрытии. "
        "Это консервативный сигнал качества активного pipeline."
    )
    return "\n".join(lines)


def format_deal_vitality_compact(vitality: DealVitality) -> str:
    label = vitality_label(vitality)
    if vitality.pipeline_confidence == "unconfirmed":
        return f"{label}; сумма = неподтверждённый pipeline"
    return label


def format_deal_vitality_for_ai(vitality: DealVitality) -> str:
    lines = [
        f"DEAL VITALITY сделки #{vitality.deal_id}",
        f"State: {vitality.state}",
        f"Pipeline confidence: {vitality.pipeline_confidence}",
        f"CRM OPPORTUNITY: {_money(vitality.opportunity)} {vitality.currency}",
        "Reasons:",
    ]
    lines.extend(f"- {reason}" for reason in vitality.reasons)
    lines.extend(
        [
            "Guardrail: CRM OPPORTUNITY is a stored deal amount, not proof that the deal "
            "is alive or that this amount is expected revenue.",
            "Guardrail: if pipeline confidence is unconfirmed, first recommend verifying "
            "actual deal status before recommending a new follow-up.",
            "Guardrail: communication history proves recorded work, not current client intent.",
            "Guardrail: never auto-close a deal from vitality state alone.",
        ]
    )
    return "\n".join(lines)
