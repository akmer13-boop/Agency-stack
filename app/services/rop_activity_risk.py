from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.rop_deal import DealDrilldown
from app.services.rop_deal_evidence import DealStageEvidence

StageRiskLevel = Literal["critical", "attention", "unmeasured"]
CommunicationEvidenceLevel = Literal["confirmed", "none_recorded", "unknown"]
NextActionState = Literal["missing", "present"]


@dataclass(frozen=True, slots=True)
class ActivityAwareDealRisk:
    deal_id: str
    stage_risk: StageRiskLevel
    stage_age_days: int | None
    stage_rule_label: str | None
    communication_evidence: CommunicationEvidenceLevel
    communications_after_stage: int | None
    last_communication_type: str | None
    days_since_last_communication: int | None
    next_action_state: NextActionState


def build_activity_aware_risk(
    report: DealDrilldown,
    evidence: DealStageEvidence,
) -> ActivityAwareDealRisk:
    if report.sla_severity == "critical":
        stage_risk: StageRiskLevel = "critical"
    elif report.sla_severity == "attention":
        stage_risk = "attention"
    else:
        stage_risk = "unmeasured"

    if evidence.completed_communications_after_stage is None:
        communication_evidence: CommunicationEvidenceLevel = "unknown"
    elif evidence.completed_communications_after_stage > 0:
        communication_evidence = "confirmed"
    else:
        communication_evidence = "none_recorded"

    return ActivityAwareDealRisk(
        deal_id=report.deal_id,
        stage_risk=stage_risk,
        stage_age_days=report.stage_age_days,
        stage_rule_label=report.sla_rule_label,
        communication_evidence=communication_evidence,
        communications_after_stage=evidence.completed_communications_after_stage,
        last_communication_type=evidence.last_communication_type,
        days_since_last_communication=evidence.days_since_last_communication,
        next_action_state=(
            "present" if evidence.next_open_activity_exists else "missing"
        ),
    )


def _stage_line(risk: ActivityAwareDealRisk) -> str:
    age = f"{risk.stage_age_days} дн." if risk.stage_age_days is not None else "возраст не установлен"
    rule = f" · {risk.stage_rule_label}" if risk.stage_rule_label else ""
    if risk.stage_risk == "critical":
        return f"🔴 Stage risk: КРИТИЧНО · {age}{rule}"
    if risk.stage_risk == "attention":
        return f"🟡 Stage risk: ВНИМАНИЕ · {age}{rule}"
    return f"⚪ Stage risk: отдельного измеряемого SLA-сигнала нет · {age}"


def _communication_line(risk: ActivityAwareDealRisk) -> str:
    if risk.communication_evidence == "confirmed":
        count = risk.communications_after_stage or 0
        last = risk.last_communication_type or "коммуникация"
        if risk.days_since_last_communication is None:
            suffix = "дата последней коммуникации не установлена"
        else:
            suffix = f"последняя {last} · {risk.days_since_last_communication} дн. назад"
        return (
            "🟢 Communication evidence: работа после входа на стадию подтверждена · "
            f"завершённых коммуникаций {count} · {suffix}"
        )
    if risk.communication_evidence == "none_recorded":
        return (
            "⚪ Communication evidence: после входа на стадию завершённые "
            "коммуникации в локальном срезе не найдены"
        )
    return "⚪ Communication evidence: недостаточно данных для временной оценки"


def _next_action_line(risk: ActivityAwareDealRisk) -> str:
    if risk.next_action_state == "missing":
        return "🔴 Next action: незавершённый следующий шаг в CRM отсутствует"
    return "🟢 Next action: незавершённый следующий шаг в CRM есть"


def _diagnosis_lines(risk: ActivityAwareDealRisk) -> list[str]:
    lines: list[str] = []

    if risk.communication_evidence == "confirmed":
        lines.append(
            "История работы после входа на текущую стадию подтверждена; нет оснований "
            "утверждать, что follow-up вообще не выполнялся."
        )
    elif risk.communication_evidence == "none_recorded":
        lines.append(
            "После входа на текущую стадию завершённые коммуникации в локальном "
            "срезе не найдены; это сигнал отсутствия данных, а не установленная причина."
        )

    if risk.stage_risk == "critical" and risk.next_action_state == "missing":
        lines.append(
            "Доказанный управленческий риск: карточка критично превышает stage-specific "
            "SLA и при этом не имеет назначенного следующего незавершённого действия."
        )
    elif risk.stage_risk == "critical":
        lines.append(
            "Доказанный управленческий риск: карточка критично превышает stage-specific SLA."
        )
    elif risk.stage_risk == "attention" and risk.next_action_state == "missing":
        lines.append(
            "Доказанный управленческий риск: карточка в зоне SLA-внимания и без "
            "следующего незавершённого действия."
        )
    elif risk.next_action_state == "missing":
        lines.append("Доказанный риск контроля: следующий шаг в CRM не назначен.")

    lines.append(
        "Причина, почему клиент или сделка не двигается дальше, этими агрегатами не установлена."
    )
    return lines


def format_activity_aware_risk(risk: ActivityAwareDealRisk) -> str:
    lines = [
        "ИИ-РОП · activity-aware risk",
        f"• {_stage_line(risk)}",
        f"• {_communication_line(risk)}",
        f"• {_next_action_line(risk)}",
        "",
        "Управленческий диагноз:",
    ]
    lines.extend(f"• {item}" for item in _diagnosis_lines(risk))
    lines.append(
        "\nСвежесть коммуникации показывается как факт. Отдельный норматив допустимой "
        "паузы между коммуникациями пока не задан, поэтому дни с последнего контакта "
        "не окрашиваются в SLA-критичность."
    )
    return "\n".join(lines)


def format_activity_aware_risk_compact(risk: ActivityAwareDealRisk) -> str:
    if risk.stage_risk == "critical":
        stage = f"🔴 стадия {risk.stage_age_days if risk.stage_age_days is not None else '—'} дн."
    elif risk.stage_risk == "attention":
        stage = f"🟡 стадия {risk.stage_age_days if risk.stage_age_days is not None else '—'} дн."
    else:
        stage = "⚪ stage SLA не измеряется"

    if risk.communication_evidence == "confirmed":
        count = risk.communications_after_stage or 0
        days = (
            f"{risk.days_since_last_communication} дн. назад"
            if risk.days_since_last_communication is not None
            else "дата не установлена"
        )
        communication = f"🟢 коммуникации {count}, последняя {days}"
    elif risk.communication_evidence == "none_recorded":
        communication = "⚪ коммуникации после входа не найдены"
    else:
        communication = "⚪ коммуникации: недостаточно данных"

    next_action = (
        "🔴 next action отсутствует"
        if risk.next_action_state == "missing"
        else "🟢 next action есть"
    )
    return f"{stage} | {communication} | {next_action}"


def format_activity_aware_risk_for_ai(risk: ActivityAwareDealRisk) -> str:
    lines = [
        f"ACTIVITY-AWARE RISK сделки #{risk.deal_id}",
        f"Stage risk: {risk.stage_risk}",
        f"Stage age days: {risk.stage_age_days if risk.stage_age_days is not None else 'unknown'}",
        f"Stage rule: {risk.stage_rule_label or 'none'}",
        f"Communication evidence: {risk.communication_evidence}",
        "Completed communications after stage entry: "
        f"{risk.communications_after_stage if risk.communications_after_stage is not None else 'unknown'}",
        f"Last communication type: {risk.last_communication_type or 'unknown'}",
        "Days since last completed communication: "
        f"{risk.days_since_last_communication if risk.days_since_last_communication is not None else 'unknown'}",
        f"Next open activity: {'missing' if risk.next_action_state == 'missing' else 'present'}",
        "Deterministic diagnosis:",
    ]
    lines.extend(f"- {item}" for item in _diagnosis_lines(risk))
    lines.extend(
        [
            "Guardrail: наличие завершённых коммуникаций после входа на стадию означает, "
            "что нельзя утверждать 'follow-up не выполнялся' только из-за stage age.",
            "Guardrail: дни с последней коммуникации являются фактом, но не отдельным SLA, "
            "пока бизнес не утвердил норматив допустимой паузы между контактами.",
            "Guardrail: причина остановки клиента не установлена без отдельного доказательства.",
        ]
    )
    return "\n".join(lines)
