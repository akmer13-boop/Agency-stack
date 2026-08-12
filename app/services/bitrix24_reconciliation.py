from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.config import Settings
from app.storage.crm_store import CrmStore

RECONCILIABLE_ENTITY_TYPES = (
    "deal",
    "lead",
    "contact",
    "company",
    "activity",
)


class BitrixReconciliationAuditStatus(StrEnum):
    COMPLETE = "complete"
    BLOCKED = "blocked"
    ANOMALY = "anomaly"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EntityReconciliationEvidence:
    entity_type: str
    observed_count: int
    local_count: int
    absence_candidate_count: int
    absence_candidate_ids: tuple[str, ...]
    observed_not_persisted_count: int
    observed_not_persisted_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BitrixReconciliationAudit:
    run_id: int
    created_at: str
    status: BitrixReconciliationAuditStatus
    authoritative: bool
    reason: str
    entity_evidence: tuple[EntityReconciliationEvidence, ...]


def _sort_entity_ids(values: set[str]) -> tuple[str, ...]:
    def key(value: str) -> tuple[int, int | str]:
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)

    return tuple(sorted(values, key=key))


class BitrixReconciliationAuditStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def write(self, audit: BitrixReconciliationAudit) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "run_id": audit.run_id,
            "created_at": audit.created_at,
            "status": audit.status.value,
            "authoritative": audit.authoritative,
            "reason": audit.reason,
            "entity_evidence": [asdict(item) for item in audit.entity_evidence],
        }

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary_path = Path(temporary.name)

        temporary_path.replace(self.path)

    def read(self) -> BitrixReconciliationAudit | None:
        if not self.path.exists():
            return None

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Bitrix reconciliation audit is unreadable: {self.path}") from exc

        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise RuntimeError(f"Bitrix reconciliation audit has invalid schema: {self.path}")

        raw_items = payload.get("entity_evidence")
        if not isinstance(raw_items, list):
            raise RuntimeError(f"Bitrix reconciliation audit has invalid evidence: {self.path}")

        evidence: list[EntityReconciliationEvidence] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise RuntimeError(f"Bitrix reconciliation audit has invalid evidence: {self.path}")
            evidence.append(
                EntityReconciliationEvidence(
                    entity_type=str(raw.get("entity_type", "")),
                    observed_count=int(raw.get("observed_count", 0)),
                    local_count=int(raw.get("local_count", 0)),
                    absence_candidate_count=int(raw.get("absence_candidate_count", 0)),
                    absence_candidate_ids=tuple(
                        str(value) for value in raw.get("absence_candidate_ids", [])
                    ),
                    observed_not_persisted_count=int(raw.get("observed_not_persisted_count", 0)),
                    observed_not_persisted_ids=tuple(
                        str(value) for value in raw.get("observed_not_persisted_ids", [])
                    ),
                )
            )

        try:
            status = BitrixReconciliationAuditStatus(str(payload.get("status", "")))
        except ValueError as exc:
            raise RuntimeError(
                f"Bitrix reconciliation audit has invalid status: {self.path}"
            ) from exc

        return BitrixReconciliationAudit(
            run_id=int(payload.get("run_id", 0)),
            created_at=str(payload.get("created_at", "")),
            status=status,
            authoritative=bool(payload.get("authoritative", False)),
            reason=str(payload.get("reason", "")),
            entity_evidence=tuple(evidence),
        )


async def build_bitrix_reconciliation_audit(
    store: CrmStore,
    *,
    run_id: int,
    observed_ids_by_type: dict[str, set[str]],
    item_limit: int | None,
    now: datetime | None = None,
) -> BitrixReconciliationAudit:
    created_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()

    if item_limit is not None:
        return BitrixReconciliationAudit(
            run_id=run_id,
            created_at=created_at,
            status=BitrixReconciliationAuditStatus.BLOCKED,
            authoritative=False,
            reason="item_limit_enabled",
            entity_evidence=(),
        )

    missing_types = [
        entity_type
        for entity_type in RECONCILIABLE_ENTITY_TYPES
        if entity_type not in observed_ids_by_type
    ]
    if missing_types:
        return BitrixReconciliationAudit(
            run_id=run_id,
            created_at=created_at,
            status=BitrixReconciliationAuditStatus.BLOCKED,
            authoritative=False,
            reason="observed_entity_types_incomplete:" + ",".join(missing_types),
            entity_evidence=(),
        )

    evidence: list[EntityReconciliationEvidence] = []
    anomaly_found = False

    for entity_type in RECONCILIABLE_ENTITY_TYPES:
        observed_ids = set(observed_ids_by_type[entity_type])
        local_ids = await store.list_entity_ids(entity_type)

        absence_candidates = local_ids - observed_ids
        observed_not_persisted = observed_ids - local_ids
        if observed_not_persisted:
            anomaly_found = True

        evidence.append(
            EntityReconciliationEvidence(
                entity_type=entity_type,
                observed_count=len(observed_ids),
                local_count=len(local_ids),
                absence_candidate_count=len(absence_candidates),
                absence_candidate_ids=_sort_entity_ids(absence_candidates),
                observed_not_persisted_count=len(observed_not_persisted),
                observed_not_persisted_ids=_sort_entity_ids(observed_not_persisted),
            )
        )

    return BitrixReconciliationAudit(
        run_id=run_id,
        created_at=created_at,
        status=(
            BitrixReconciliationAuditStatus.ANOMALY
            if anomaly_found
            else BitrixReconciliationAuditStatus.COMPLETE
        ),
        authoritative=not anomaly_found,
        reason=("observed_ids_not_persisted" if anomaly_found else "full_unlimited_sync_completed"),
        entity_evidence=tuple(evidence),
    )


async def build_and_store_bitrix_reconciliation_audit(
    settings: Settings,
    store: CrmStore,
    *,
    run_id: int,
    observed_ids_by_type: dict[str, set[str]],
    item_limit: int | None,
) -> BitrixReconciliationAudit:
    audit = await build_bitrix_reconciliation_audit(
        store,
        run_id=run_id,
        observed_ids_by_type=observed_ids_by_type,
        item_limit=item_limit,
    )
    BitrixReconciliationAuditStore(settings.bitrix24_reconciliation_audit_file).write(audit)
    return audit


def get_last_bitrix_reconciliation_audit(
    settings: Settings,
) -> BitrixReconciliationAudit | None:
    return BitrixReconciliationAuditStore(settings.bitrix24_reconciliation_audit_file).read()


def format_bitrix_reconciliation_audit(
    audit: BitrixReconciliationAudit | None,
) -> str:
    if audit is None:
        return (
            "Bitrix24 reconciliation audit ещё не создавался.\n"
            "Он появляется только после полного read-only sync."
        )

    lines = [
        "Bitrix24 · Deleted Entity Reconciliation Audit",
        "Режим: DRY RUN — raw CRM сущности НЕ удаляются; сам "
        "reconciliation не применяет и не снимает tombstones.",
        f"Run: #{audit.run_id}",
        f"Статус: {audit.status.value}",
        f"Authoritative: {'да' if audit.authoritative else 'нет'}",
        f"Причина: {audit.reason}",
    ]

    for item in audit.entity_evidence:
        lines.append(
            f"• {item.entity_type}: observed {item.observed_count}, "
            f"local {item.local_count}, absence candidates "
            f"{item.absence_candidate_count}, "
            f"observed-not-persisted {item.observed_not_persisted_count}"
        )

    lines.extend(
        [
            "",
            "Absence candidate означает только: ID есть в локальной SQLite, "
            "но отсутствовал в завершённом полном обходе Bitrix24.",
            "Absence candidate сам по себе — НЕ доказательство удаления. "
            "Сам audit не меняет analytics visibility; soft tombstone может быть "
            "применён только отдельной fail-closed activation после повторной "
            "read-only проверки кандидата.",
            "Incremental sync и full sync с лимитом не считаются authoritative для reconciliation.",
        ]
    )
    return "\n".join(lines)
