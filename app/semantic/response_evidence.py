from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.semantic.activity_classifier import (
    ActivityClassification,
    classify_activity,
)
from app.semantic.models import SemanticActivity, SemanticLead

_TECHNICAL_FUTURE_YEAR = 2099


@dataclass(frozen=True, slots=True)
class LeadResponseEvidence:
    lead_id: str
    lead_created_at: datetime
    first_manager_evidence_at: datetime | None
    first_manager_evidence_activity_id: str | None
    first_manager_timestamp_source: str | None
    first_confirmed_communication_at: datetime | None
    first_confirmed_communication_activity_id: str | None
    first_communication_timestamp_source: str | None
    manager_evidence_elapsed_seconds: float | None
    confirmed_communication_elapsed_seconds: float | None
    considered_activities: int
    ignored_pre_creation_activities: int
    activities_without_observed_timestamp: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResponseEvidenceContract:
    period_start: datetime
    observed_until: datetime
    leads: tuple[LeadResponseEvidence, ...]
    skipped_leads_without_created_at: int
    warnings: tuple[str, ...] = ()

    @property
    def cohort_size(self) -> int:
        return len(self.leads)


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _usable_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    value = value.astimezone(UTC)
    if value.year >= _TECHNICAL_FUTURE_YEAR:
        return None
    return value


def activity_observed_at(
    activity: SemanticActivity,
) -> tuple[datetime | None, str | None]:
    candidates = (
        ("END_TIME", activity.ended_at),
        ("START_TIME", activity.started_at),
        ("LAST_UPDATED", activity.updated_at),
        ("CREATED", activity.created_at),
    )

    for source, value in candidates:
        usable = _usable_timestamp(value)
        if usable is not None:
            return usable, source

    return None, None


def _first_event(
    candidates: list[tuple[datetime, str, str]],
) -> tuple[datetime | None, str | None, str | None]:
    if not candidates:
        return None, None, None

    event_at, activity_id, source = min(
        candidates,
        key=lambda item: (item[0], item[1]),
    )
    return event_at, activity_id, source


def build_response_evidence_contract(
    leads: list[SemanticLead],
    activities: list[SemanticActivity],
    *,
    period_start: datetime,
    observed_until: datetime,
    manager_actor_ids: frozenset[str] | None = None,
) -> ResponseEvidenceContract:
    start = _aware_utc(period_start, field="period_start")
    end = _aware_utc(observed_until, field="observed_until")

    if start > end:
        raise ValueError("period_start must be <= observed_until")

    skipped_missing_created = 0
    cohort: list[SemanticLead] = []

    for lead in leads:
        if lead.created_at is None:
            skipped_missing_created += 1
            continue

        created_at = lead.created_at.astimezone(UTC)
        if start <= created_at <= end:
            cohort.append(lead)

    activities_by_lead: dict[str, list[SemanticActivity]] = {}
    for activity in activities:
        if activity.owner_entity_type != "1" or not activity.owner_entity_id:
            continue
        activities_by_lead.setdefault(activity.owner_entity_id, []).append(activity)

    result: list[LeadResponseEvidence] = []

    for lead in sorted(cohort, key=lambda item: (item.created_at or end, item.id)):
        created_at = lead.created_at
        if created_at is None:
            continue

        created_at = created_at.astimezone(UTC)
        manager_candidates: list[tuple[datetime, str, str]] = []
        communication_candidates: list[tuple[datetime, str, str]] = []
        considered = 0
        ignored_pre_creation = 0
        without_timestamp = 0

        for activity in activities_by_lead.get(lead.id, []):
            event_at, timestamp_source = activity_observed_at(activity)

            if event_at is None or timestamp_source is None:
                without_timestamp += 1
                continue

            if event_at < created_at:
                ignored_pre_creation += 1
                continue

            if event_at > end:
                continue

            considered += 1
            evidence = classify_activity(activity)

            if evidence.is_manager_evidence and (
                manager_actor_ids is None or activity.responsible_user_id in manager_actor_ids
            ):
                manager_candidates.append((event_at, activity.id, timestamp_source))

            if evidence.classification is ActivityClassification.CONFIRMED_COMMUNICATION:
                communication_candidates.append((event_at, activity.id, timestamp_source))

        (
            first_manager_at,
            first_manager_activity_id,
            first_manager_source,
        ) = _first_event(manager_candidates)

        (
            first_communication_at,
            first_communication_activity_id,
            first_communication_source,
        ) = _first_event(communication_candidates)

        manager_delay = (
            max(0.0, (first_manager_at - created_at).total_seconds())
            if first_manager_at is not None
            else None
        )
        communication_delay = (
            max(0.0, (first_communication_at - created_at).total_seconds())
            if first_communication_at is not None
            else None
        )

        warnings: list[str] = []

        if first_manager_source in {"LAST_UPDATED", "CREATED"}:
            warnings.append(f"manager_timestamp_fallback={first_manager_source}")

        if first_communication_source in {"LAST_UPDATED", "CREATED"}:
            warnings.append(f"communication_timestamp_fallback={first_communication_source}")

        if without_timestamp:
            warnings.append(f"activities_without_observed_timestamp={without_timestamp}")

        result.append(
            LeadResponseEvidence(
                lead_id=lead.id,
                lead_created_at=created_at,
                first_manager_evidence_at=first_manager_at,
                first_manager_evidence_activity_id=first_manager_activity_id,
                first_manager_timestamp_source=first_manager_source,
                first_confirmed_communication_at=first_communication_at,
                first_confirmed_communication_activity_id=(first_communication_activity_id),
                first_communication_timestamp_source=first_communication_source,
                manager_evidence_elapsed_seconds=manager_delay,
                confirmed_communication_elapsed_seconds=communication_delay,
                considered_activities=considered,
                ignored_pre_creation_activities=ignored_pre_creation,
                activities_without_observed_timestamp=without_timestamp,
                warnings=tuple(warnings),
            )
        )

    contract_warnings: list[str] = []
    if skipped_missing_created:
        contract_warnings.append(f"leads_without_created_at={skipped_missing_created}")

    return ResponseEvidenceContract(
        period_start=start,
        observed_until=end,
        leads=tuple(result),
        skipped_leads_without_created_at=skipped_missing_created,
        warnings=tuple(contract_warnings),
    )
