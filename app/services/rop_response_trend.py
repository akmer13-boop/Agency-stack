from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from math import ceil
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.semantic.models import SemanticLead
from app.semantic.repository import SemanticRepository
from app.semantic.response_evidence import build_response_evidence_contract

_COHORT_DAYS = 7
_DEFAULT_OBSERVATION_HORIZON_DAYS = 7


@dataclass(frozen=True, slots=True)
class ResponseEvidenceWeek:
    start_at: datetime
    end_at: datetime
    total_leads: int
    manager_evidence_leads: int
    confirmed_communication_leads: int
    manager_coverage_percent: float | None
    communication_coverage_percent: float | None
    manager_median_seconds: float | None
    manager_p90_seconds: float | None
    communication_median_seconds: float | None
    communication_p90_seconds: float | None


@dataclass(frozen=True, slots=True)
class ResponseEvidenceTrendDelta:
    manager_coverage_delta_pp: float | None
    communication_coverage_delta_pp: float | None
    manager_median_delta_seconds: float | None
    manager_p90_delta_seconds: float | None
    communication_median_delta_seconds: float | None
    communication_p90_delta_seconds: float | None
    manager_coverage_direction: str
    communication_coverage_direction: str
    manager_median_direction: str
    manager_p90_direction: str
    communication_median_direction: str
    communication_p90_direction: str


@dataclass(frozen=True, slots=True)
class ResponseEvidenceTrendReport:
    timezone_name: str
    weeks_requested: int
    observation_horizon_days: int
    generated_at: datetime
    cohorts: tuple[ResponseEvidenceWeek, ...]
    latest_vs_previous: ResponseEvidenceTrendDelta | None


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _week_start(value: datetime, zone: ZoneInfo) -> datetime:
    local = value.astimezone(zone)
    midnight = datetime.combine(local.date(), time.min, tzinfo=zone)
    return midnight - timedelta(days=local.weekday())


def _percent(part: int, total: int) -> float | None:
    if total <= 0:
        return None
    return 100.0 * part / total


def _median(values: list[float]) -> float | None:
    return float(median(values)) if values else None


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, ceil(0.90 * len(ordered)))
    return float(ordered[rank - 1])


def _delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return current - previous


def _coverage_direction(delta: float | None) -> str:
    if delta is None:
        return "unavailable"
    if delta > 0:
        return "higher"
    if delta < 0:
        return "lower"
    return "same"


def _elapsed_direction(delta: float | None) -> str:
    if delta is None:
        return "unavailable"
    if delta < 0:
        return "faster"
    if delta > 0:
        return "slower"
    return "same"


def _cohort_leads(
    leads: list[SemanticLead],
    *,
    start_at: datetime,
    end_at: datetime,
) -> list[SemanticLead]:
    result: list[SemanticLead] = []
    for lead in leads:
        if lead.created_at is None:
            continue
        created = lead.created_at.astimezone(UTC)
        if start_at <= created < end_at:
            result.append(lead)
    return result


def _week_from_contract(
    *,
    start_at: datetime,
    end_at: datetime,
    contract,
    horizon_seconds: float,
) -> ResponseEvidenceWeek:
    manager_delays = [
        item.manager_evidence_elapsed_seconds
        for item in contract.leads
        if item.manager_evidence_elapsed_seconds is not None
        and item.manager_evidence_elapsed_seconds <= horizon_seconds
    ]
    communication_delays = [
        item.confirmed_communication_elapsed_seconds
        for item in contract.leads
        if item.confirmed_communication_elapsed_seconds is not None
        and item.confirmed_communication_elapsed_seconds <= horizon_seconds
    ]

    total = contract.cohort_size
    return ResponseEvidenceWeek(
        start_at=start_at,
        end_at=end_at,
        total_leads=total,
        manager_evidence_leads=len(manager_delays),
        confirmed_communication_leads=len(communication_delays),
        manager_coverage_percent=_percent(len(manager_delays), total),
        communication_coverage_percent=_percent(len(communication_delays), total),
        manager_median_seconds=_median(manager_delays),
        manager_p90_seconds=_p90(manager_delays),
        communication_median_seconds=_median(communication_delays),
        communication_p90_seconds=_p90(communication_delays),
    )


def _compare(
    current: ResponseEvidenceWeek,
    previous: ResponseEvidenceWeek,
) -> ResponseEvidenceTrendDelta:
    manager_coverage = _delta(
        current.manager_coverage_percent,
        previous.manager_coverage_percent,
    )
    communication_coverage = _delta(
        current.communication_coverage_percent,
        previous.communication_coverage_percent,
    )
    manager_median = _delta(
        current.manager_median_seconds,
        previous.manager_median_seconds,
    )
    manager_p90 = _delta(
        current.manager_p90_seconds,
        previous.manager_p90_seconds,
    )
    communication_median = _delta(
        current.communication_median_seconds,
        previous.communication_median_seconds,
    )
    communication_p90 = _delta(
        current.communication_p90_seconds,
        previous.communication_p90_seconds,
    )

    return ResponseEvidenceTrendDelta(
        manager_coverage_delta_pp=manager_coverage,
        communication_coverage_delta_pp=communication_coverage,
        manager_median_delta_seconds=manager_median,
        manager_p90_delta_seconds=manager_p90,
        communication_median_delta_seconds=communication_median,
        communication_p90_delta_seconds=communication_p90,
        manager_coverage_direction=_coverage_direction(manager_coverage),
        communication_coverage_direction=_coverage_direction(communication_coverage),
        manager_median_direction=_elapsed_direction(manager_median),
        manager_p90_direction=_elapsed_direction(manager_p90),
        communication_median_direction=_elapsed_direction(communication_median),
        communication_p90_direction=_elapsed_direction(communication_p90),
    )


async def build_response_evidence_trend(
    settings: Settings,
    weeks: int = 4,
    *,
    now: datetime | None = None,
    observation_horizon_days: int = _DEFAULT_OBSERVATION_HORIZON_DAYS,
) -> ResponseEvidenceTrendReport:
    if weeks < 2 or weeks > 12:
        raise ValueError("Response evidence trend weeks must be from 2 to 12")
    if observation_horizon_days < 1 or observation_horizon_days > 30:
        raise ValueError("Observation horizon must be from 1 to 30 days")

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    zone = _timezone(settings.rop_timezone)
    maturity_cutoff = reference - timedelta(days=observation_horizon_days)
    latest_end_local = _week_start(maturity_cutoff, zone)

    repository = SemanticRepository(settings.database_path)
    leads = await repository.leads()
    activities = await repository.activities()

    horizon_seconds = float(observation_horizon_days * 24 * 60 * 60)
    cohorts: list[ResponseEvidenceWeek] = []

    for offset in reversed(range(weeks)):
        end_local = latest_end_local - timedelta(days=_COHORT_DAYS * offset)
        start_local = end_local - timedelta(days=_COHORT_DAYS)
        start_at = start_local.astimezone(UTC)
        end_at = end_local.astimezone(UTC)

        selected_leads = _cohort_leads(
            leads,
            start_at=start_at,
            end_at=end_at,
        )

        contract = build_response_evidence_contract(
            selected_leads,
            activities,
            period_start=start_at,
            observed_until=reference,
        )

        cohorts.append(
            _week_from_contract(
                start_at=start_at,
                end_at=end_at,
                contract=contract,
                horizon_seconds=horizon_seconds,
            )
        )

    comparison = _compare(cohorts[-1], cohorts[-2]) if len(cohorts) >= 2 else None

    return ResponseEvidenceTrendReport(
        timezone_name=settings.rop_timezone,
        weeks_requested=weeks,
        observation_horizon_days=observation_horizon_days,
        generated_at=reference,
        cohorts=tuple(cohorts),
        latest_vs_previous=comparison,
    )


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "нет наблюдений"

    sign = "-" if seconds < 0 else ""
    total_minutes = int(round(abs(seconds) / 60))
    if total_minutes < 60:
        return f"{sign}{total_minutes} мин"

    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return f"{sign}{hours} ч {minutes} мин"

    days, hours = divmod(hours, 24)
    return f"{sign}{days} д {hours} ч"


def _coverage(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _delta_pp(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f} п.п."


def _date(value: datetime, timezone_name: str) -> str:
    return value.astimezone(_timezone(timezone_name)).strftime("%Y-%m-%d")


def format_response_evidence_trend_for_ai(
    report: ResponseEvidenceTrendReport,
) -> str:
    lines = [
        "ИИ-РОП · Lead Response Evidence Trend",
        f"• Завершённых зрелых недель: {len(report.cohorts)}",
        f"• Timezone: {report.timezone_name}",
        f"• Технический observation horizon на лид: {report.observation_horizon_days}×24ч",
        "",
        "Недели (cohort = DATE_CREATE лида):",
    ]

    for item in report.cohorts:
        lines.append(
            "• "
            f"{_date(item.start_at, report.timezone_name)} — "
            f"{_date(item.end_at, report.timezone_name)} | "
            f"n={item.total_leads} | "
            f"manager coverage {_coverage(item.manager_coverage_percent)} | "
            f"manager median {_duration(item.manager_median_seconds)} | "
            f"manager p90 {_duration(item.manager_p90_seconds)} | "
            "communication coverage "
            f"{_coverage(item.communication_coverage_percent)} | "
            "communication median "
            f"{_duration(item.communication_median_seconds)} | "
            f"communication p90 {_duration(item.communication_p90_seconds)}"
        )

    delta = report.latest_vs_previous
    if delta is not None:
        lines.extend(
            [
                "",
                "Последняя зрелая неделя vs предыдущая:",
                "• manager coverage: "
                f"{_delta_pp(delta.manager_coverage_delta_pp)} "
                f"({delta.manager_coverage_direction})",
                "• manager median: "
                f"{_duration(delta.manager_median_delta_seconds)} "
                f"({delta.manager_median_direction})",
                "• manager p90: "
                f"{_duration(delta.manager_p90_delta_seconds)} "
                f"({delta.manager_p90_direction})",
                "• communication coverage: "
                f"{_delta_pp(delta.communication_coverage_delta_pp)} "
                f"({delta.communication_coverage_direction})",
                "• communication median: "
                f"{_duration(delta.communication_median_delta_seconds)} "
                f"({delta.communication_median_direction})",
                "• communication p90: "
                f"{_duration(delta.communication_p90_delta_seconds)} "
                f"({delta.communication_p90_direction})",
            ]
        )

    lines.extend(
        [
            "",
            "Методология / ограничения:",
            "• используются непересекающиеся календарные недели в ROP_TIMEZONE;",
            "• в тренд попадают только недели, полностью созревшие на технический "
            "observation horizon;",
            "• evidence засчитывается только если первое событие произошло внутри "
            "observation horizon после DATE_CREATE;",
            "• horizon нужен для сопоставимости недель и НЕ является SLA threshold;",
            "• faster/slower и higher/lower — только описательные направления изменения;",
            "• статистическая значимость и причинность не оцениваются;",
            "• это observed CRM evidence, а не First Response SLA;",
            "• business-hours, holidays и historical reassignment не применяются;",
            "• отчёт не используется как рейтинг менеджеров.",
        ]
    )

    return "\n".join(lines)
