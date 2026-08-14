from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.rop_fact_quality import build_fact_quality_report
from app.services.rop_first_response_policy import build_first_response_policy

BUSINESS_POLICY_KEYS = (
    "first_response_sla",
    "stale_deal",
    "proposal_stale",
    "business_conversion",
    "manager_rating",
    "sales_plan_fact",
    "management_escalation",
)

_POLICY_LABELS = {
    "first_response_sla": "First Response SLA",
    "stale_deal": "Stale deal",
    "proposal_stale": "Proposal follow-up",
    "business_conversion": "Business conversion",
    "manager_rating": "Manager rating",
    "sales_plan_fact": "Sales plan / fact",
    "management_escalation": "Management escalation",
}

_POLICY_COVERAGE_DEPENDENCIES = {
    "first_response_sla": (
        "lead.created_at",
        "sales_activity.responsible_user_id",
        "sales_activity.observed_timestamp",
        "observed_manager_id.directory_mapping",
    ),
    "stale_deal": (
        "deal.updated_at",
        "sales_activity.owner_entity_id",
        "sales_activity.observed_timestamp",
    ),
    "proposal_stale": (
        "deal.stage_id",
        "deal.stage_history_owner_match",
        "sales_activity.observed_timestamp",
    ),
    "business_conversion": (
        "lead.status_id",
        "lead.status_semantic",
        "lead.stage_history_owner_match",
        "deal.stage_id",
        "deal.stage_semantic",
        "deal.stage_history_owner_match",
    ),
    "manager_rating": (
        "deal.assigned_user_id",
        "lead.assigned_user_id",
        "sales_activity.responsible_user_id",
        "observed_manager_id.directory_mapping",
    ),
    "sales_plan_fact": (
        "deal.assigned_user_id",
        "deal.stage_semantic",
        "deal.currency",
        "observed_manager_id.directory_mapping",
    ),
    "management_escalation": (
        "deal.assigned_user_id",
        "deal.stage_id",
        "sales_activity.observed_timestamp",
        "observed_manager_id.directory_mapping",
    ),
}


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class BindingState(StrEnum):
    PENDING_BUSINESS_APPROVAL = "pending_business_approval"
    APPROVED_NOT_BOUND = "approved_not_bound"
    REJECTED_BY_BUSINESS = "rejected_by_business"
    REGISTRY_INVALID = "registry_invalid"


@dataclass(frozen=True, slots=True)
class BusinessPolicyDocumentEntry:
    key: str
    approval_status: ApprovalStatus
    approved_by: str
    approved_at: str
    parameters: dict[str, Any]
    note: str


@dataclass(frozen=True, slots=True)
class BusinessPolicyDocument:
    source_path: str
    schema_version: int
    valid: bool
    policies: tuple[BusinessPolicyDocumentEntry, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageGap:
    key: str
    missing: int
    total: int


@dataclass(frozen=True, slots=True)
class BusinessPolicyStatus:
    key: str
    label: str
    approval_status: ApprovalStatus
    binding_state: BindingState
    operational: bool
    configuration_state: str
    approved_by: str
    approved_at: str
    parameters: dict[str, Any]
    data_dependencies: tuple[str, ...]
    data_gaps: tuple[CoverageGap, ...]
    unavailable_coverage_keys: tuple[str, ...]
    blockers: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class BusinessPolicyRegistrySnapshot:
    source_path: str
    schema_version: int
    valid: bool
    policies: tuple[BusinessPolicyStatus, ...]
    blockers: tuple[str, ...]


def _invalid_document(path: Path, *blockers: str) -> BusinessPolicyDocument:
    return BusinessPolicyDocument(
        source_path=str(path),
        schema_version=0,
        valid=False,
        policies=(),
        blockers=tuple(blockers),
    )


def _clean_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_business_policy_document(path: str) -> BusinessPolicyDocument:
    """Load the business policy document with strict fail-closed validation."""

    source = Path(path)

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _invalid_document(source, "registry_file_missing")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _invalid_document(source, "registry_file_unreadable_or_invalid_json")

    if not isinstance(raw, dict):
        return _invalid_document(source, "registry_root_must_be_object")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        return _invalid_document(source, "unsupported_schema_version")

    policies_raw = raw.get("policies")
    if not isinstance(policies_raw, dict):
        return _invalid_document(source, "policies_must_be_object")

    expected = set(BUSINESS_POLICY_KEYS)
    actual = set(policies_raw)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)

    blockers: list[str] = []
    if missing:
        blockers.append("missing_policy_keys:" + ",".join(missing))
    if unexpected:
        blockers.append("unexpected_policy_keys:" + ",".join(unexpected))
    if blockers:
        return _invalid_document(source, *blockers)

    entries: list[BusinessPolicyDocumentEntry] = []

    for key in BUSINESS_POLICY_KEYS:
        value = policies_raw[key]
        if not isinstance(value, dict):
            return _invalid_document(source, f"policy_entry_must_be_object:{key}")

        status_raw = _clean_text(value.get("approval_status")).lower()
        try:
            approval_status = ApprovalStatus(status_raw)
        except ValueError:
            return _invalid_document(source, f"invalid_approval_status:{key}")

        if approval_status is ApprovalStatus.UNKNOWN:
            return _invalid_document(source, f"unknown_not_allowed_in_config:{key}")

        parameters = value.get("parameters", {})
        if not isinstance(parameters, dict):
            return _invalid_document(source, f"parameters_must_be_object:{key}")

        entries.append(
            BusinessPolicyDocumentEntry(
                key=key,
                approval_status=approval_status,
                approved_by=_clean_text(value.get("approved_by")),
                approved_at=_clean_text(value.get("approved_at")),
                parameters=dict(parameters),
                note=_clean_text(value.get("note")),
            )
        )

    return BusinessPolicyDocument(
        source_path=str(source),
        schema_version=1,
        valid=True,
        policies=tuple(entries),
        blockers=(),
    )


def _binding_state(status: ApprovalStatus) -> BindingState:
    if status is ApprovalStatus.APPROVED:
        return BindingState.APPROVED_NOT_BOUND
    if status is ApprovalStatus.REJECTED:
        return BindingState.REJECTED_BY_BUSINESS
    return BindingState.PENDING_BUSINESS_APPROVAL


async def build_business_policy_registry(
    settings: Settings,
) -> BusinessPolicyRegistrySnapshot:
    """Build one fail-closed view of business approvals and data prerequisites."""

    document = load_business_policy_document(settings.rop_business_policy_path)
    quality = await build_fact_quality_report(settings.database_path)
    coverage = {item.key: item for item in quality.coverages}
    first_response = build_first_response_policy(settings)

    if not document.valid:
        policies = tuple(
            BusinessPolicyStatus(
                key=key,
                label=_POLICY_LABELS[key],
                approval_status=ApprovalStatus.UNKNOWN,
                binding_state=BindingState.REGISTRY_INVALID,
                operational=False,
                configuration_state=(
                    first_response.state.value if key == "first_response_sla" else "not_bound"
                ),
                approved_by="",
                approved_at="",
                parameters={},
                data_dependencies=_POLICY_COVERAGE_DEPENDENCIES[key],
                data_gaps=(),
                unavailable_coverage_keys=(),
                blockers=("registry_invalid",),
                note="",
            )
            for key in BUSINESS_POLICY_KEYS
        )
        return BusinessPolicyRegistrySnapshot(
            source_path=document.source_path,
            schema_version=document.schema_version,
            valid=False,
            policies=policies,
            blockers=document.blockers,
        )

    statuses: list[BusinessPolicyStatus] = []

    for entry in document.policies:
        dependencies = _POLICY_COVERAGE_DEPENDENCIES[entry.key]
        gaps: list[CoverageGap] = []
        unavailable: list[str] = []

        for key in dependencies:
            fact = coverage.get(key)
            if fact is None:
                unavailable.append(key)
                continue
            if fact.missing > 0:
                gaps.append(
                    CoverageGap(
                        key=key,
                        missing=fact.missing,
                        total=fact.total,
                    )
                )

        blockers: list[str] = []
        if entry.approval_status is ApprovalStatus.PENDING:
            blockers.append("business_approval_pending")
        elif entry.approval_status is ApprovalStatus.APPROVED:
            blockers.append("technical_binding_not_implemented")
            if not entry.approved_by or not entry.approved_at:
                blockers.append("approval_metadata_missing")
        else:
            blockers.append("rejected_by_business")

        if unavailable:
            blockers.append("coverage_evidence_unavailable")

        statuses.append(
            BusinessPolicyStatus(
                key=entry.key,
                label=_POLICY_LABELS[entry.key],
                approval_status=entry.approval_status,
                binding_state=_binding_state(entry.approval_status),
                operational=False,
                configuration_state=(
                    first_response.state.value if entry.key == "first_response_sla" else "not_bound"
                ),
                approved_by=entry.approved_by,
                approved_at=entry.approved_at,
                parameters=entry.parameters,
                data_dependencies=dependencies,
                data_gaps=tuple(gaps),
                unavailable_coverage_keys=tuple(unavailable),
                blockers=tuple(blockers),
                note=entry.note,
            )
        )

    return BusinessPolicyRegistrySnapshot(
        source_path=document.source_path,
        schema_version=document.schema_version,
        valid=True,
        policies=tuple(statuses),
        blockers=(),
    )


def format_business_policy_registry_for_ai(
    snapshot: BusinessPolicyRegistrySnapshot,
) -> str:
    operational_count = sum(item.operational for item in snapshot.policies)

    lines = [
        "ИИ-РОП · Business Policy Registry",
        f"• registry file: {snapshot.source_path}",
        f"• schema version: {snapshot.schema_version}",
        f"• registry valid: {'yes' if snapshot.valid else 'no'}",
        f"• operational business rules: {operational_count}/{len(snapshot.policies)}",
    ]

    if snapshot.blockers:
        lines.append("• registry blockers: " + ", ".join(snapshot.blockers))

    lines.extend(["", "POLICIES:"])

    for item in snapshot.policies:
        lines.append(
            f"• {item.key} ({item.label}): approval={item.approval_status.value}; "
            f"binding={item.binding_state.value}; operational=no; "
            f"configuration={item.configuration_state}"
        )

        if item.approved_by or item.approved_at:
            lines.append(
                f"  approval metadata: by={item.approved_by or 'NOT SET'}; "
                f"at={item.approved_at or 'NOT SET'}"
            )

        if item.data_gaps:
            gap_text = ", ".join(
                f"{gap.key} missing {gap.missing}/{gap.total}" for gap in item.data_gaps
            )
            lines.append(f"  observed data gaps: {gap_text}")
        else:
            lines.append("  observed data gaps: none in measured dependencies")

        if item.unavailable_coverage_keys:
            lines.append("  coverage not measured: " + ", ".join(item.unavailable_coverage_keys))

        if item.blockers:
            lines.append("  blockers: " + ", ".join(item.blockers))

        if item.note:
            lines.append(f"  note: {item.note}")

    lines.extend(
        [
            "",
            "GUARDRAILS:",
            "• business approval is NOT technical activation;",
            ("• an approved rule remains non-operational until explicit binding is implemented;"),
            ("• First Response configuration READY does NOT mean business approval or SLA active;"),
            ("• source-data coverage is descriptive and is NOT a business acceptance threshold;"),
            (
                "• no KPI, SLA compliance, rating, stale verdict, plan/fact or "
                "escalation is calculated;"
            ),
            ("• no CRM write, automatic policy activation or data repair is performed here."),
        ]
    )

    return "\n".join(lines)
