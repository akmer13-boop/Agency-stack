from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.semantic.models import SemanticLead


@dataclass(frozen=True, slots=True)
class CountMetricContract:
    metric: str
    period_start: datetime
    period_end: datetime
    timezone: str
    value: int
    sample_size: int
    source_entity_ids: tuple[str, ...]
    calculated_at: datetime
    warnings: tuple[str, ...] = ()


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def build_new_leads_metric(
    leads: list[SemanticLead],
    *,
    period_start: datetime,
    period_end: datetime,
    timezone_name: str,
    calculated_at: datetime | None = None,
) -> CountMetricContract:
    """Build the deterministic M01 new-leads metric from semantic lead objects."""

    start = _aware_utc(period_start, field="period_start")
    end = _aware_utc(period_end, field="period_end")
    calculated = _aware_utc(
        calculated_at or datetime.now(UTC),
        field="calculated_at",
    )

    if start > end:
        raise ValueError("period_start must be <= period_end")

    matched_ids: list[str] = []
    missing_created_at = 0

    for lead in leads:
        if lead.created_at is None:
            missing_created_at += 1
            continue
        if start <= lead.created_at <= end:
            matched_ids.append(lead.id)

    warnings: list[str] = []
    if missing_created_at:
        warnings.append(f"leads_without_created_at={missing_created_at}")

    return CountMetricContract(
        metric="new_leads",
        period_start=start,
        period_end=end,
        timezone=timezone_name,
        value=len(matched_ids),
        sample_size=len(leads),
        source_entity_ids=tuple(matched_ids),
        calculated_at=calculated,
        warnings=tuple(warnings),
    )
