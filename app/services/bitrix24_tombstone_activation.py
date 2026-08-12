from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.config import Settings
from app.integrations.bitrix24 import (
    Bitrix24ProbeResult,
    Bitrix24ReadOnlyClient,
    Bitrix24RequestError,
)
from app.proxy import build_proxy_url
from app.services.bitrix24_reconciliation import (
    BitrixReconciliationAudit,
    BitrixReconciliationAuditStatus,
)
from app.storage.crm_store import CrmStore, CrmTombstone

DIRECT_GET_METHODS = {
    "deal": "crm.deal.get",
    "lead": "crm.lead.get",
    "contact": "crm.contact.get",
    "company": "crm.company.get",
}

ACCESS_MARKERS = (
    "access denied",
    "access_denied",
    "insufficient_scope",
    "invalid_credentials",
    "user_access_error",
)

MISSING_MARKERS = (
    "not found",
    "is not found",
    "does not exist",
)


class TombstoneVerificationStatus(StrEnum):
    EXISTS = "exists"
    CONFIRMED_MISSING = "confirmed_missing"
    ACCESS_DENIED = "access_denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TombstoneCandidateVerification:
    entity_type: str
    entity_id: str
    status: TombstoneVerificationStatus
    evidence_kind: str
    detail: str
    verified_at: str


@dataclass(frozen=True, slots=True)
class BitrixTombstoneActivationResult:
    source_audit_run_id: int
    candidate_count: int
    newly_tombstoned: int
    already_tombstoned: int
    verification_counts: dict[str, int]
    tombstone_counts: dict[str, int]
    raw_counts_before: dict[str, int]
    raw_counts_after: dict[str, int]
    active_counts_before: dict[str, int]
    active_counts_after: dict[str, int]


class BitrixTombstoneActivationBlocked(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        verifications: tuple[TombstoneCandidateVerification, ...] = (),
    ) -> None:
        super().__init__(message)
        self.verifications = verifications


def build_tombstone_probe_client(settings: Settings) -> Bitrix24ReadOnlyClient:
    return Bitrix24ReadOnlyClient(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_sync_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_sync_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
    )


def _candidate_pairs(
    audit: BitrixReconciliationAudit,
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for evidence in audit.entity_evidence:
        for entity_id in evidence.absence_candidate_ids:
            pairs.append((evidence.entity_type, entity_id))
    return tuple(pairs)


def _classify_direct_get(
    entity_type: str,
    entity_id: str,
    probe: Bitrix24ProbeResult,
) -> TombstoneCandidateVerification:
    verified_at = datetime.now(UTC).isoformat()
    data = probe.data

    if 200 <= probe.status_code < 300:
        result = data.get("result")
        if isinstance(result, dict) and str(result.get("ID", "")) == entity_id:
            return TombstoneCandidateVerification(
                entity_type=entity_type,
                entity_id=entity_id,
                status=TombstoneVerificationStatus.EXISTS,
                evidence_kind="direct_get_exists",
                detail="Bitrix24 returned the entity by ID",
                verified_at=verified_at,
            )
        return TombstoneCandidateVerification(
            entity_type=entity_type,
            entity_id=entity_id,
            status=TombstoneVerificationStatus.ERROR,
            evidence_kind="direct_get_unexpected_result",
            detail="Bitrix24 returned an unexpected successful response",
            verified_at=verified_at,
        )

    raw_error = str(data.get("error") or "")
    raw_description = str(data.get("error_description") or "")
    combined = f"{raw_error} {raw_description}".strip().lower()

    if probe.status_code == 403 or any(marker in combined for marker in ACCESS_MARKERS):
        return TombstoneCandidateVerification(
            entity_type=entity_type,
            entity_id=entity_id,
            status=TombstoneVerificationStatus.ACCESS_DENIED,
            evidence_kind="direct_get_access_denied",
            detail=(raw_description or raw_error or f"HTTP {probe.status_code}")[:200],
            verified_at=verified_at,
        )

    if any(marker in combined for marker in MISSING_MARKERS):
        return TombstoneCandidateVerification(
            entity_type=entity_type,
            entity_id=entity_id,
            status=TombstoneVerificationStatus.CONFIRMED_MISSING,
            evidence_kind="direct_get_not_found",
            detail=(raw_description or raw_error or "Not found")[:200],
            verified_at=verified_at,
        )

    return TombstoneCandidateVerification(
        entity_type=entity_type,
        entity_id=entity_id,
        status=TombstoneVerificationStatus.ERROR,
        evidence_kind="direct_get_unclassified_error",
        detail=(raw_description or raw_error or f"HTTP {probe.status_code}")[:200],
        verified_at=verified_at,
    )


async def _probe_with_retries(
    client: Bitrix24ReadOnlyClient,
    settings: Settings,
    method: str,
    params: dict,
) -> Bitrix24ProbeResult:
    attempts = settings.bitrix24_sync_retry_attempts
    last_error: Bitrix24RequestError | None = None

    for attempt in range(1, attempts + 1):
        try:
            probe = await client.probe(method, params)
        except Bitrix24RequestError as exc:
            last_error = exc
            if attempt == attempts:
                raise
            await asyncio.sleep(settings.bitrix24_sync_retry_backoff_seconds * attempt)
            continue

        if probe.status_code == 429 or probe.status_code >= 500:
            if attempt < attempts:
                await asyncio.sleep(settings.bitrix24_sync_retry_backoff_seconds * attempt)
                continue
        return probe

    if last_error is not None:
        raise last_error
    raise Bitrix24RequestError("Bitrix24 verification failed")


async def _verify_activity_control(
    client: Bitrix24ReadOnlyClient,
    settings: Settings,
    store: CrmStore,
    candidate_ids: set[str],
) -> str:
    control_id = await store.find_active_entity_id(
        "activity",
        excluded_ids=candidate_ids,
    )
    if control_id is None:
        raise BitrixTombstoneActivationBlocked(
            "Activity verification has no positive control entity"
        )

    probe = await _probe_with_retries(
        client,
        settings,
        "crm.activity.list",
        {
            "filter": {"ID": int(control_id)},
            "select": ["ID"],
            "start": 0,
        },
    )

    if probe.status_code == 403:
        raise BitrixTombstoneActivationBlocked(
            "Activity verification control returned access denied"
        )
    if probe.status_code != 200:
        raise BitrixTombstoneActivationBlocked(
            f"Activity verification control returned HTTP {probe.status_code}"
        )

    result = probe.data.get("result")
    if not isinstance(result, list):
        raise BitrixTombstoneActivationBlocked(
            "Activity verification control returned invalid result"
        )

    returned_ids = {
        str(item.get("ID"))
        for item in result
        if isinstance(item, dict) and item.get("ID") is not None
    }
    if control_id not in returned_ids:
        raise BitrixTombstoneActivationBlocked(
            "Activity verification positive control did not return its own ID"
        )
    return control_id


async def _verify_activity_candidate(
    client: Bitrix24ReadOnlyClient,
    settings: Settings,
    entity_id: str,
) -> TombstoneCandidateVerification:
    verified_at = datetime.now(UTC).isoformat()
    probe = await _probe_with_retries(
        client,
        settings,
        "crm.activity.list",
        {
            "filter": {"ID": int(entity_id)},
            "select": ["ID"],
            "start": 0,
        },
    )

    data = probe.data
    raw_error = str(data.get("error") or "")
    raw_description = str(data.get("error_description") or "")
    combined = f"{raw_error} {raw_description}".strip().lower()

    if probe.status_code == 403 or any(marker in combined for marker in ACCESS_MARKERS):
        return TombstoneCandidateVerification(
            entity_type="activity",
            entity_id=entity_id,
            status=TombstoneVerificationStatus.ACCESS_DENIED,
            evidence_kind="activity_exact_id_list_access_denied",
            detail=(raw_description or raw_error or f"HTTP {probe.status_code}")[:200],
            verified_at=verified_at,
        )

    if probe.status_code != 200:
        return TombstoneCandidateVerification(
            entity_type="activity",
            entity_id=entity_id,
            status=TombstoneVerificationStatus.ERROR,
            evidence_kind="activity_exact_id_list_http_error",
            detail=(raw_description or raw_error or f"HTTP {probe.status_code}")[:200],
            verified_at=verified_at,
        )

    result = data.get("result")
    if not isinstance(result, list):
        return TombstoneCandidateVerification(
            entity_type="activity",
            entity_id=entity_id,
            status=TombstoneVerificationStatus.ERROR,
            evidence_kind="activity_exact_id_list_invalid_result",
            detail="Bitrix24 returned an invalid activity list result",
            verified_at=verified_at,
        )

    returned_ids = {
        str(item.get("ID"))
        for item in result
        if isinstance(item, dict) and item.get("ID") is not None
    }

    if entity_id in returned_ids:
        return TombstoneCandidateVerification(
            entity_type="activity",
            entity_id=entity_id,
            status=TombstoneVerificationStatus.EXISTS,
            evidence_kind="activity_exact_id_list_exists",
            detail="Exact-ID activity list returned the entity",
            verified_at=verified_at,
        )

    if returned_ids:
        return TombstoneCandidateVerification(
            entity_type="activity",
            entity_id=entity_id,
            status=TombstoneVerificationStatus.ERROR,
            evidence_kind="activity_exact_id_list_mismatched_result",
            detail="Exact-ID activity list returned a different ID",
            verified_at=verified_at,
        )

    return TombstoneCandidateVerification(
        entity_type="activity",
        entity_id=entity_id,
        status=TombstoneVerificationStatus.CONFIRMED_MISSING,
        evidence_kind="activity_exact_id_list_empty_controlled",
        detail="Exact-ID activity list returned no rows after positive control",
        verified_at=verified_at,
    )


async def verify_tombstone_candidates(
    settings: Settings,
    audit: BitrixReconciliationAudit,
    *,
    client: Bitrix24ReadOnlyClient | None = None,
    store: CrmStore | None = None,
) -> tuple[TombstoneCandidateVerification, ...]:
    if audit.status is not BitrixReconciliationAuditStatus.COMPLETE:
        raise BitrixTombstoneActivationBlocked(
            f"Reconciliation audit status is {audit.status.value}, not complete"
        )
    if not audit.authoritative:
        raise BitrixTombstoneActivationBlocked("Reconciliation audit is not authoritative")
    if audit.reason != "full_unlimited_sync_completed":
        raise BitrixTombstoneActivationBlocked(
            f"Reconciliation audit reason is not activation-safe: {audit.reason}"
        )

    pairs = _candidate_pairs(audit)
    if not pairs:
        return ()

    unsupported = sorted(
        {
            entity_type
            for entity_type, _entity_id in pairs
            if entity_type not in {*DIRECT_GET_METHODS, "activity"}
        }
    )
    if unsupported:
        raise BitrixTombstoneActivationBlocked(
            "Unsupported reconciliation candidate types: " + ", ".join(unsupported)
        )

    crm_store = store or CrmStore(settings.database_path)
    await crm_store.initialize()
    bitrix_client = client or build_tombstone_probe_client(settings)

    activity_ids = {entity_id for entity_type, entity_id in pairs if entity_type == "activity"}
    if activity_ids:
        await _verify_activity_control(
            bitrix_client,
            settings,
            crm_store,
            activity_ids,
        )

    verifications: list[TombstoneCandidateVerification] = []

    for entity_type, entity_id in pairs:
        try:
            if entity_type == "activity":
                verification = await _verify_activity_candidate(
                    bitrix_client,
                    settings,
                    entity_id,
                )
            else:
                method = DIRECT_GET_METHODS[entity_type]
                probe = await _probe_with_retries(
                    bitrix_client,
                    settings,
                    method,
                    {"id": int(entity_id)},
                )
                verification = _classify_direct_get(
                    entity_type,
                    entity_id,
                    probe,
                )
        except Bitrix24RequestError as exc:
            verification = TombstoneCandidateVerification(
                entity_type=entity_type,
                entity_id=entity_id,
                status=TombstoneVerificationStatus.ERROR,
                evidence_kind="verification_request_error",
                detail=exc.public_message[:200],
                verified_at=datetime.now(UTC).isoformat(),
            )

        verifications.append(verification)

    return tuple(verifications)


async def activate_bitrix_tombstones(
    settings: Settings,
    audit: BitrixReconciliationAudit,
    *,
    client: Bitrix24ReadOnlyClient | None = None,
    store: CrmStore | None = None,
) -> BitrixTombstoneActivationResult:
    crm_store = store or CrmStore(settings.database_path)
    await crm_store.initialize()

    verifications = await verify_tombstone_candidates(
        settings,
        audit,
        client=client,
        store=crm_store,
    )

    verification_counts = Counter(item.status.value for item in verifications)

    unsafe = tuple(
        item
        for item in verifications
        if item.status is not TombstoneVerificationStatus.CONFIRMED_MISSING
    )
    if unsafe:
        summary = ", ".join(
            f"{status}={count}" for status, count in sorted(verification_counts.items())
        )
        raise BitrixTombstoneActivationBlocked(
            "Tombstone activation blocked by live verification: " + summary,
            verifications=verifications,
        )

    candidate_pairs = {(item.entity_type, item.entity_id) for item in verifications}
    existing_tombstones = {
        (item.entity_type, item.entity_id) for item in await crm_store.list_tombstones()
    }
    already_tombstoned_pairs = candidate_pairs & existing_tombstones
    new_pairs = candidate_pairs - existing_tombstones

    raw_before = await crm_store.count_by_type()
    active_before = await crm_store.count_active_by_type()

    tombstones = [
        CrmTombstone(
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            source_audit_run_id=audit.run_id,
            evidence_kind=item.evidence_kind,
            evidence_verified_at=item.verified_at,
        )
        for item in verifications
    ]

    applied = await crm_store.apply_tombstones(tombstones)
    if applied != len(tombstones):
        raise RuntimeError("Tombstone store reported an unexpected applied-row count")

    raw_after = await crm_store.count_by_type()
    active_after = await crm_store.count_active_by_type()
    tombstone_counts = await crm_store.count_tombstones()

    if raw_after != raw_before:
        raise RuntimeError("Raw CRM counts changed during soft-tombstone activation")

    new_by_type = Counter(entity_type for entity_type, _entity_id in new_pairs)
    checked_types = set(raw_before) | set(active_before) | set(active_after) | set(new_by_type)
    for entity_type in checked_types:
        expected = active_before.get(entity_type, 0) - new_by_type.get(entity_type, 0)
        actual = active_after.get(entity_type, 0)
        if actual != expected:
            raise RuntimeError(
                "Active CRM count mismatch after tombstone activation for "
                f"{entity_type}: expected {expected}, got {actual}"
            )

    return BitrixTombstoneActivationResult(
        source_audit_run_id=audit.run_id,
        candidate_count=len(candidate_pairs),
        newly_tombstoned=len(new_pairs),
        already_tombstoned=len(already_tombstoned_pairs),
        verification_counts=dict(sorted(verification_counts.items())),
        tombstone_counts=tombstone_counts,
        raw_counts_before=raw_before,
        raw_counts_after=raw_after,
        active_counts_before=active_before,
        active_counts_after=active_after,
    )


async def get_tombstone_counts(settings: Settings) -> dict[str, int]:
    store = CrmStore(settings.database_path)
    await store.initialize()
    return await store.count_tombstones()


def format_tombstone_counts(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    lines = [
        "Bitrix24 · Soft Tombstones",
        f"Всего активных tombstones: {total}",
    ]
    if not counts:
        lines.append("Tombstones пока не применялись.")
        return "\n".join(lines)

    for entity_type, count in sorted(counts.items()):
        lines.append(f"• {entity_type}: {count}")
    lines.extend(
        [
            "",
            "Raw CRM payload сохранён; tombstones влияют только на active analytics view.",
        ]
    )
    return "\n".join(lines)


def format_tombstone_activation_result(
    result: BitrixTombstoneActivationResult,
) -> str:
    lines = [
        "Bitrix24 · Soft Tombstone Activation",
        f"Source full-sync audit: #{result.source_audit_run_id}",
        f"Проверено кандидатов: {result.candidate_count}",
        f"Новых tombstones: {result.newly_tombstoned}",
        f"Уже были tombstoned: {result.already_tombstoned}",
        "Live verification:",
    ]
    for status, count in sorted(result.verification_counts.items()):
        lines.append(f"• {status}: {count}")

    lines.append("Tombstones по типам:")
    if result.tombstone_counts:
        for entity_type, count in sorted(result.tombstone_counts.items()):
            lines.append(f"• {entity_type}: {count}")
    else:
        lines.append("• none")

    lines.extend(
        [
            "",
            "Raw CRM payload: сохранён.",
            "История стадий: не tombstoned.",
            "Bitrix24 write: не выполнялся.",
            "Если сущность снова появится в sync, tombstone снимется автоматически.",
        ]
    )
    return "\n".join(lines)
