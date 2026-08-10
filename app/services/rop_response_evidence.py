from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from statistics import median

from app.config import Settings
from app.semantic.repository import SemanticRepository
from app.semantic.response_evidence import build_response_evidence_contract


@dataclass(frozen=True, slots=True)
class LeadResponseEvidenceReport:
    days: int
    start_at: datetime
    end_at: datetime
    total_leads: int
    leads_with_manager_evidence: int
    leads_with_confirmed_communication: int
    manager_evidence_median_seconds: float | None
    manager_evidence_p90_seconds: float | None
    communication_median_seconds: float | None
    communication_p90_seconds: float | None
    manager_timestamp_sources: tuple[tuple[str, int], ...]
    communication_timestamp_sources: tuple[tuple[str, int], ...]
    manager_timestamp_fallbacks: int
    communication_timestamp_fallbacks: int
    skipped_leads_without_created_at: int


def _p90(values: list[float]) -> float | None:
    if not values:
        return None

    ordered = sorted(values)
    rank = max(1, ceil(0.90 * len(ordered)))
    return float(ordered[rank - 1])


def _median(values: list[float]) -> float | None:
    return float(median(values)) if values else None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "нет наблюдений"

    total_minutes = int(round(seconds / 60))
    if total_minutes < 60:
        return f"{total_minutes} мин"

    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин"

    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч"


async def build_lead_response_evidence_report(
    settings: Settings,
    days: int = 7,
    *,
    now: datetime | None = None,
) -> LeadResponseEvidenceReport:
    if days < 1 or days > 365:
        raise ValueError("Response evidence period must be from 1 to 365 days")

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    start_at = reference - timedelta(days=days)

    repository = SemanticRepository(settings.database_path)
    leads = await repository.leads()
    activities = await repository.activities()

    contract = build_response_evidence_contract(
        leads,
        activities,
        period_start=start_at,
        observed_until=reference,
    )

    manager_delays = [
        item.manager_evidence_elapsed_seconds
        for item in contract.leads
        if item.manager_evidence_elapsed_seconds is not None
    ]
    communication_delays = [
        item.confirmed_communication_elapsed_seconds
        for item in contract.leads
        if item.confirmed_communication_elapsed_seconds is not None
    ]

    manager_sources: Counter[str] = Counter(
        item.first_manager_timestamp_source
        for item in contract.leads
        if item.first_manager_timestamp_source is not None
    )
    communication_sources: Counter[str] = Counter(
        item.first_communication_timestamp_source
        for item in contract.leads
        if item.first_communication_timestamp_source is not None
    )

    manager_fallbacks = sum(
        count for source, count in manager_sources.items() if source in {"LAST_UPDATED", "CREATED"}
    )
    communication_fallbacks = sum(
        count
        for source, count in communication_sources.items()
        if source in {"LAST_UPDATED", "CREATED"}
    )

    return LeadResponseEvidenceReport(
        days=days,
        start_at=start_at,
        end_at=reference,
        total_leads=contract.cohort_size,
        leads_with_manager_evidence=len(manager_delays),
        leads_with_confirmed_communication=len(communication_delays),
        manager_evidence_median_seconds=_median(manager_delays),
        manager_evidence_p90_seconds=_p90(manager_delays),
        communication_median_seconds=_median(communication_delays),
        communication_p90_seconds=_p90(communication_delays),
        manager_timestamp_sources=tuple(manager_sources.most_common()),
        communication_timestamp_sources=tuple(communication_sources.most_common()),
        manager_timestamp_fallbacks=manager_fallbacks,
        communication_timestamp_fallbacks=communication_fallbacks,
        skipped_leads_without_created_at=(contract.skipped_leads_without_created_at),
    )


def _percent(part: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{100 * part / total:.1f}%"


def _source_line(values: tuple[tuple[str, int], ...]) -> str:
    if not values:
        return "нет timestamp evidence"
    return ", ".join(f"{source} {count}" for source, count in values)


def format_lead_response_evidence_for_ai(
    report: LeadResponseEvidenceReport,
) -> str:
    no_manager = report.total_leads - report.leads_with_manager_evidence
    no_communication = report.total_leads - report.leads_with_confirmed_communication

    lines = [
        f"ИИ-РОП · Lead Response Evidence · rolling {report.days}×24ч",
        f"• Лидов в когорте: {report.total_leads}",
        "• С наблюдаемым manager-side evidence: "
        f"{report.leads_with_manager_evidence} "
        f"({_percent(report.leads_with_manager_evidence, report.total_leads)})",
        f"• Без manager-side evidence: {no_manager}",
        "• С подтверждённой CRM-коммуникацией: "
        f"{report.leads_with_confirmed_communication} "
        f"({_percent(report.leads_with_confirmed_communication, report.total_leads)})",
        f"• Без подтверждённой коммуникации: {no_communication}",
        "• Calendar elapsed до первого manager-side evidence · median: "
        f"{_format_duration(report.manager_evidence_median_seconds)}",
        "• Calendar elapsed до первого manager-side evidence · p90: "
        f"{_format_duration(report.manager_evidence_p90_seconds)}",
        "• Calendar elapsed до первой confirmed communication · median: "
        f"{_format_duration(report.communication_median_seconds)}",
        "• Calendar elapsed до первой confirmed communication · p90: "
        f"{_format_duration(report.communication_p90_seconds)}",
        f"• Timestamp sources manager evidence: {_source_line(report.manager_timestamp_sources)}",
        "• Timestamp sources communication: "
        f"{_source_line(report.communication_timestamp_sources)}",
        "• Fallback timestamps manager/communication: "
        f"{report.manager_timestamp_fallbacks}/"
        f"{report.communication_timestamp_fallbacks}",
    ]

    if report.skipped_leads_without_created_at:
        lines.append(
            f"• Лидов без DATE_CREATE вне когорты: {report.skipped_leads_without_created_at}"
        )

    lines.extend(
        [
            "",
            "Методология / ограничения:",
            "• это observed CRM evidence, а не First Response SLA;",
            "• старт наблюдения = DATE_CREATE лида только как техническая точка "
            "отсчёта; нормативное начало SLA заказчиком не утверждено;",
            "• elapsed считается в календарном времени; рабочие часы, праздники "
            "и выходные не вычитаются;",
            "• manager-side evidence использует консервативную классификацию Stage 4.2;",
            "• входящая коммуникация и meeting не приписываются менеджеру как "
            "manager-side evidence автоматически;",
            "• история смены ответственного здесь не восстанавливается, поэтому "
            "этот отчёт не используется для рейтинга конкретного менеджера;",
            "• никаких SLA thresholds, pass/fail или просрочки здесь нет.",
        ]
    )

    return "\n".join(lines)
