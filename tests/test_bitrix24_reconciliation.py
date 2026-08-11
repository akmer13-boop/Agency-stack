from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.services.bitrix24_reconciliation import (
    RECONCILIABLE_ENTITY_TYPES,
    BitrixReconciliationAuditStatus,
    BitrixReconciliationAuditStore,
    build_and_store_bitrix_reconciliation_audit,
    build_bitrix_reconciliation_audit,
    format_bitrix_reconciliation_audit,
)
from app.services.bitrix24_sync import _sync_pages
from app.storage.crm_store import CrmStore


def _observed(**overrides: set[str]) -> dict[str, set[str]]:
    values = {entity_type: set() for entity_type in RECONCILIABLE_ENTITY_TYPES}
    values.update(overrides)
    return values


def test_default_audit_path_follows_database_directory(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "agency.db"),
    )

    assert settings.bitrix24_reconciliation_audit_file == str(
        tmp_path / "bitrix_reconciliation_audit.json"
    )


@pytest.mark.asyncio
async def test_authoritative_audit_detects_local_absence_candidates(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "deal",
        [
            {"ID": "1", "TITLE": "One"},
            {"ID": "2", "TITLE": "Two"},
            {"ID": "3", "TITLE": "Three"},
        ],
    )

    audit = await build_bitrix_reconciliation_audit(
        store,
        run_id=10,
        observed_ids_by_type=_observed(deal={"1", "2"}),
        item_limit=None,
        now=datetime(2026, 8, 11, 20, 0, tzinfo=UTC),
    )

    assert audit.status is BitrixReconciliationAuditStatus.COMPLETE
    assert audit.authoritative is True
    deal = next(item for item in audit.entity_evidence if item.entity_type == "deal")
    assert deal.observed_count == 2
    assert deal.local_count == 3
    assert deal.absence_candidate_count == 1
    assert deal.absence_candidate_ids == ("3",)


@pytest.mark.asyncio
async def test_audit_is_blocked_when_sync_item_limit_is_enabled(
    tmp_path: Path,
) -> None:
    store = CrmStore(str(tmp_path / "crm.db"))
    await store.initialize()

    audit = await build_bitrix_reconciliation_audit(
        store,
        run_id=11,
        observed_ids_by_type=_observed(),
        item_limit=100,
    )

    assert audit.status is BitrixReconciliationAuditStatus.BLOCKED
    assert audit.authoritative is False
    assert audit.reason == "item_limit_enabled"
    assert audit.entity_evidence == ()


@pytest.mark.asyncio
async def test_audit_is_blocked_when_observed_entity_types_are_incomplete(
    tmp_path: Path,
) -> None:
    store = CrmStore(str(tmp_path / "crm.db"))
    await store.initialize()

    audit = await build_bitrix_reconciliation_audit(
        store,
        run_id=12,
        observed_ids_by_type={"deal": set()},
        item_limit=None,
    )

    assert audit.status is BitrixReconciliationAuditStatus.BLOCKED
    assert audit.authoritative is False
    assert audit.reason.startswith("observed_entity_types_incomplete:")


@pytest.mark.asyncio
async def test_observed_but_not_persisted_is_anomaly(
    tmp_path: Path,
) -> None:
    store = CrmStore(str(tmp_path / "crm.db"))
    await store.initialize()

    audit = await build_bitrix_reconciliation_audit(
        store,
        run_id=13,
        observed_ids_by_type=_observed(deal={"999"}),
        item_limit=None,
    )

    assert audit.status is BitrixReconciliationAuditStatus.ANOMALY
    assert audit.authoritative is False
    deal = next(item for item in audit.entity_evidence if item.entity_type == "deal")
    assert deal.observed_not_persisted_ids == ("999",)


@pytest.mark.asyncio
async def test_audit_round_trip_does_not_modify_crm_rows(tmp_path: Path) -> None:
    database_path = str(tmp_path / "crm.db")
    audit_path = str(tmp_path / "reconciliation.json")
    settings = Settings(
        _env_file=None,
        database_path=database_path,
        bitrix24_reconciliation_audit_path=audit_path,
    )
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities("lead", [{"ID": "7"}])

    audit = await build_and_store_bitrix_reconciliation_audit(
        settings,
        store,
        run_id=14,
        observed_ids_by_type=_observed(lead=set()),
        item_limit=None,
    )
    loaded = BitrixReconciliationAuditStore(audit_path).read()

    assert loaded == audit
    assert Path(audit_path).exists()
    assert await store.list_entity_ids("lead") == {"7"}


@pytest.mark.asyncio
async def test_sync_pages_collects_observed_ids_without_changing_upsert_semantics(
    tmp_path: Path,
) -> None:
    store = CrmStore(str(tmp_path / "crm.db"))
    await store.initialize()
    run_id = await store.start_run()
    counts: dict[str, int] = {}
    observed: set[str] = set()

    async def pages():
        yield [{"ID": "20", "DATE_MODIFY": "2026-08-11"}]
        yield [{"ID": "21", "DATE_MODIFY": "2026-08-11"}]

    await _sync_pages(
        store,
        run_id,
        counts,
        "deal",
        pages(),
        "DATE_MODIFY",
        observed_ids=observed,
    )

    assert observed == {"20", "21"}
    assert counts["deal"] == 2
    assert await store.list_entity_ids("deal") == {"20", "21"}


def test_formatter_explicitly_keeps_dry_run_semantics() -> None:
    empty_text = format_bitrix_reconciliation_audit(None)
    assert "полного read-only sync" in empty_text

    audit = __import__(
        "app.services.bitrix24_reconciliation",
        fromlist=["BitrixReconciliationAudit"],
    ).BitrixReconciliationAudit(
        run_id=1,
        created_at="2026-08-11T20:00:00+00:00",
        status=BitrixReconciliationAuditStatus.COMPLETE,
        authoritative=True,
        reason="full_unlimited_sync_completed",
        entity_evidence=(),
    )
    text = format_bitrix_reconciliation_audit(audit)

    assert "DRY RUN" in text
    assert "НЕ удаляются" in text
    assert "НЕ доказательство удаления" in text
