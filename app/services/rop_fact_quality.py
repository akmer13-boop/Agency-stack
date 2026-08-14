from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from app.semantic.activity_classifier import classify_activity
from app.semantic.repository import SemanticRepository
from app.semantic.response_evidence import activity_observed_at
from app.services.rop_actor_resolution import ActorKind, build_actor_resolution_report

_SALES_OWNER_TYPES = frozenset({"1", "2"})


@dataclass(frozen=True, slots=True)
class CoverageFact:
    key: str
    covered: int
    total: int

    @property
    def missing(self) -> int:
        return max(0, self.total - self.covered)

    @property
    def percent(self) -> float | None:
        if self.total <= 0:
            return None
        return 100.0 * self.covered / self.total


@dataclass(frozen=True, slots=True)
class FactQualityReport:
    generated_at: datetime
    deal_count: int
    lead_count: int
    sales_activity_count: int
    actor_ids_observed: int
    actor_ids_resolved: int
    coverages: tuple[CoverageFact, ...]
    activity_classes: tuple[tuple[str, int], ...]
    notes: tuple[str, ...]


def _coverage(key: str, values: list[bool]) -> CoverageFact:
    return CoverageFact(key=key, covered=sum(values), total=len(values))


async def build_fact_quality_report(
    database_path: str,
    *,
    now: datetime | None = None,
) -> FactQualityReport:
    """Measure descriptive source-field coverage without business pass/fail thresholds."""

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    repository = SemanticRepository(database_path)
    deals = await repository.deals()
    leads = await repository.leads()
    activities = await repository.activities()
    deal_history = await repository.deal_stage_history()
    lead_history = await repository.lead_stage_history()

    sales_activities = [item for item in activities if item.owner_entity_type in _SALES_OWNER_TYPES]
    deal_history_owners = {
        item.owner_entity_id for item in deal_history if item.owner_entity_id is not None
    }
    lead_history_owners = {
        item.owner_entity_id for item in lead_history if item.owner_entity_id is not None
    }

    actor_report = await build_actor_resolution_report(database_path, now=reference)
    resolved_actor_ids = {
        item.actor_id for item in actor_report.actors if item.kind is not ActorKind.UNRESOLVED_ACTOR
    }

    activity_classes: Counter[str] = Counter()
    activity_has_timestamp: list[bool] = []
    for activity in sales_activities:
        activity_classes[classify_activity(activity).classification.value] += 1
        observed_at, _source = activity_observed_at(activity)
        activity_has_timestamp.append(observed_at is not None)

    coverages = (
        _coverage("deal.assigned_user_id", [item.assigned_user_id is not None for item in deals]),
        _coverage("deal.created_at", [item.created_at is not None for item in deals]),
        _coverage("deal.updated_at", [item.updated_at is not None for item in deals]),
        _coverage("deal.stage_id", [item.stage_id is not None for item in deals]),
        _coverage("deal.stage_semantic", [item.stage_semantic is not None for item in deals]),
        _coverage("deal.currency", [item.currency is not None for item in deals]),
        _coverage(
            "deal.stage_history_owner_match",
            [item.id in deal_history_owners for item in deals],
        ),
        _coverage("lead.assigned_user_id", [item.assigned_user_id is not None for item in leads]),
        _coverage("lead.created_at", [item.created_at is not None for item in leads]),
        _coverage("lead.updated_at", [item.updated_at is not None for item in leads]),
        _coverage("lead.status_id", [item.status_id is not None for item in leads]),
        _coverage("lead.status_semantic", [item.status_semantic is not None for item in leads]),
        _coverage("lead.source_id", [item.source_id is not None for item in leads]),
        _coverage(
            "lead.stage_history_owner_match",
            [item.id in lead_history_owners for item in leads],
        ),
        _coverage(
            "sales_activity.owner_entity_id",
            [item.owner_entity_id is not None for item in sales_activities],
        ),
        _coverage(
            "sales_activity.responsible_user_id",
            [item.responsible_user_id is not None for item in sales_activities],
        ),
        _coverage("sales_activity.observed_timestamp", activity_has_timestamp),
        CoverageFact(
            key="observed_actor_id.resolution",
            covered=len(resolved_actor_ids),
            total=actor_report.observed,
        ),
    )

    return FactQualityReport(
        generated_at=reference,
        deal_count=len(deals),
        lead_count=len(leads),
        sales_activity_count=len(sales_activities),
        actor_ids_observed=actor_report.observed,
        actor_ids_resolved=len(resolved_actor_ids),
        coverages=coverages,
        activity_classes=tuple(
            sorted(activity_classes.items(), key=lambda item: (-item[1], item[0]))
        ),
        notes=(
            "Coverage is descriptive only; no business acceptance threshold is applied.",
            (
                "Stage-history owner match means at least one stored history event "
                "exists for the current entity ID."
            ),
            (
                "Observed activity timestamp uses the conservative timestamp contract "
                "already used by response evidence."
            ),
            (
                "Actor resolution counts directory users and conservative special-actor "
                "candidates as resolved identity types; unresolved actors remain gaps."
            ),
            (
                "Actor identity resolution does not establish a human sales-manager role "
                "and does not authorize ranking or performance conclusions."
            ),
        ),
    )


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def format_fact_quality_for_ai(report: FactQualityReport) -> str:
    lines = [
        "ИИ-РОП · Management Fact Quality & Coverage",
        f"Generated UTC: {report.generated_at.isoformat()}",
        f"• current deals: {report.deal_count}",
        f"• current leads: {report.lead_count}",
        f"• sales activities linked to lead/deal: {report.sales_activity_count}",
        (
            "• observed responsible/assigned actor IDs with resolved identity type: "
            f"{report.actor_ids_resolved}/{report.actor_ids_observed}"
        ),
        "",
        "FIELD / EVIDENCE COVERAGE — DESCRIPTIVE, NO PASS/FAIL THRESHOLD:",
    ]
    for item in report.coverages:
        lines.append(
            f"• {item.key}: {item.covered}/{item.total} "
            f"({_percent(item.percent)}), missing {item.missing}"
        )

    if report.activity_classes:
        lines.extend(["", "ACTIVITY EVIDENCE CLASSES:"])
        for name, count in report.activity_classes:
            lines.append(f"• {name}: {count}")

    lines.extend(["", "GUARDRAILS:"])
    for note in report.notes:
        lines.append(f"• {note}")
    lines.extend(
        [
            "• Do not call a metric reliable/unreliable from these percentages alone.",
            "• Do not invent a minimum acceptable coverage percentage.",
            "• Business-policy readiness is separate from source-data coverage.",
            "• No CRM write, no data repair and no automatic backfill is performed here.",
        ]
    )
    return "\n".join(lines)
