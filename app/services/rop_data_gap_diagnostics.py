from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.semantic.repository import SemanticRepository
from app.semantic.response_evidence import activity_observed_at
from app.services.rop_actor_resolution import ActorKind, build_actor_resolution_report

_SALES_OWNER_TYPES = frozenset({"1", "2"})
_MAX_IDS_PER_GAP = 50


@dataclass(frozen=True, slots=True)
class DataGapDetail:
    key: str
    total: int
    missing: int
    entity_ids: tuple[str, ...]
    truncated: int


@dataclass(frozen=True, slots=True)
class UnresolvedActorImpact:
    actor_id: str
    deal_references: int
    lead_references: int
    activity_references: int
    technical_signals: tuple[str, ...]

    @property
    def total_references(self) -> int:
        return self.deal_references + self.lead_references + self.activity_references


@dataclass(frozen=True, slots=True)
class DataGapDiagnostics:
    generated_at: datetime
    gaps: tuple[DataGapDetail, ...]
    unresolved_actors: tuple[UnresolvedActorImpact, ...]
    notes: tuple[str, ...]


def _gap(key: str, total: int, missing_ids: list[str]) -> DataGapDetail | None:
    if not missing_ids:
        return None
    ordered = sorted(
        set(missing_ids),
        key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
    )
    shown = tuple(ordered[:_MAX_IDS_PER_GAP])
    return DataGapDetail(
        key=key,
        total=total,
        missing=len(ordered),
        entity_ids=shown,
        truncated=max(0, len(ordered) - len(shown)),
    )


async def build_data_gap_diagnostics(
    database_path: str,
    *,
    now: datetime | None = None,
) -> DataGapDiagnostics:
    """Return exact local IDs behind descriptive CRM data-coverage gaps."""

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

    candidates = (
        _gap(
            "deal.assigned_user_id",
            len(deals),
            [item.id for item in deals if item.assigned_user_id is None],
        ),
        _gap("deal.created_at", len(deals), [item.id for item in deals if item.created_at is None]),
        _gap("deal.updated_at", len(deals), [item.id for item in deals if item.updated_at is None]),
        _gap("deal.stage_id", len(deals), [item.id for item in deals if item.stage_id is None]),
        _gap(
            "deal.stage_semantic",
            len(deals),
            [item.id for item in deals if item.stage_semantic is None],
        ),
        _gap("deal.currency", len(deals), [item.id for item in deals if item.currency is None]),
        _gap(
            "deal.stage_history_owner_match",
            len(deals),
            [item.id for item in deals if item.id not in deal_history_owners],
        ),
        _gap(
            "lead.assigned_user_id",
            len(leads),
            [item.id for item in leads if item.assigned_user_id is None],
        ),
        _gap("lead.created_at", len(leads), [item.id for item in leads if item.created_at is None]),
        _gap("lead.updated_at", len(leads), [item.id for item in leads if item.updated_at is None]),
        _gap("lead.status_id", len(leads), [item.id for item in leads if item.status_id is None]),
        _gap(
            "lead.status_semantic",
            len(leads),
            [item.id for item in leads if item.status_semantic is None],
        ),
        _gap("lead.source_id", len(leads), [item.id for item in leads if item.source_id is None]),
        _gap(
            "lead.stage_history_owner_match",
            len(leads),
            [item.id for item in leads if item.id not in lead_history_owners],
        ),
        _gap(
            "sales_activity.owner_entity_id",
            len(sales_activities),
            [item.id for item in sales_activities if item.owner_entity_id is None],
        ),
        _gap(
            "sales_activity.responsible_user_id",
            len(sales_activities),
            [item.id for item in sales_activities if item.responsible_user_id is None],
        ),
        _gap(
            "sales_activity.observed_timestamp",
            len(sales_activities),
            [item.id for item in sales_activities if activity_observed_at(item)[0] is None],
        ),
    )
    gaps = [item for item in candidates if item is not None]

    actor_report = await build_actor_resolution_report(database_path, now=reference)
    unresolved = tuple(
        item for item in actor_report.actors if item.kind is ActorKind.UNRESOLVED_ACTOR
    )
    actor_gap = _gap(
        "observed_actor_id.resolution",
        actor_report.observed,
        [item.actor_id for item in unresolved],
    )
    if actor_gap is not None:
        gaps.append(actor_gap)

    impacts = tuple(
        UnresolvedActorImpact(
            actor_id=item.actor_id,
            deal_references=item.deal_references,
            lead_references=item.lead_references,
            activity_references=item.activity_references,
            technical_signals=item.technical_signals,
        )
        for item in unresolved
    )

    return DataGapDiagnostics(
        generated_at=reference,
        gaps=tuple(gaps),
        unresolved_actors=impacts,
        notes=(
            "Diagnostics use only the current active local CRM view.",
            "Entity IDs are identifiers only; client text and contacts are excluded.",
            (
                "Actor resolution is separate from employee-directory mapping: a "
                "conservative special_actor_candidate may be resolved without being "
                "treated as a human manager."
            ),
            (
                "unresolved_actor means identity type is not established; it is not "
                "proof that the user was deleted, inactive or fired."
            ),
            "No missing value is repaired or written back to Bitrix24.",
        ),
    )


def format_data_gap_diagnostics_for_ai(report: DataGapDiagnostics) -> str:
    lines = [
        "ИИ-РОП · Data Gap Diagnostics",
        f"Generated UTC: {report.generated_at.isoformat()}",
        "",
        "EXACT CURRENT DATA GAPS:",
    ]
    if not report.gaps:
        lines.append("• none in the measured fields/evidence")
    else:
        for gap in report.gaps:
            ids = ", ".join(gap.entity_ids) or "none"
            suffix = f" (+{gap.truncated} more)" if gap.truncated else ""
            lines.append(f"• {gap.key}: missing {gap.missing}/{gap.total}; IDs: {ids}{suffix}")

    lines.extend(["", "UNRESOLVED RESPONSIBLE / ASSIGNED ACTOR IDS:"])
    if not report.unresolved_actors:
        lines.append("• none")
    else:
        for item in report.unresolved_actors:
            signals = ", ".join(item.technical_signals) or "none"
            lines.append(
                f"• ID {item.actor_id}: deals {item.deal_references}; "
                f"leads {item.lead_references}; activities {item.activity_references}; "
                f"total references {item.total_references}; signals {signals}"
            )

    lines.extend(["", "GUARDRAILS:"])
    for note in report.notes:
        lines.append(f"• {note}")
    lines.extend(
        [
            "• Do not infer the business importance of a gap from its count alone.",
            (
                "• Do not call an unresolved actor deleted, inactive or fired "
                "without separate evidence."
            ),
            "• Do not invent replacement values for missing CRM fields.",
            "• This tool performs no CRM write and no automatic repair.",
        ]
    )
    return "\n".join(lines)
