from __future__ import annotations

import pytest

from app.services.rop_fact_quality import (
    build_fact_quality_report,
    format_fact_quality_for_ai,
)
from app.storage.crm_store import CrmStore, CrmTombstone


@pytest.mark.asyncio
async def test_fact_quality_uses_active_entities_and_measures_coverage(tmp_path) -> None:
    database_path = str(tmp_path / "quality.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities("user", [{"ID": "1", "NAME": "Anna", "ACTIVE": True}])
    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "10",
                "ASSIGNED_BY_ID": "1",
                "DATE_CREATE": "2026-08-10T10:00:00Z",
                "DATE_MODIFY": "2026-08-10T11:00:00Z",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
                "CURRENCY_ID": "RUB",
            },
            {
                "ID": "11",
                "ASSIGNED_BY_ID": "2",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
            },
            {
                "ID": "12",
                "ASSIGNED_BY_ID": "1",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
            },
        ],
    )
    await store.apply_tombstones(
        [
            CrmTombstone(
                entity_type="deal",
                entity_id="12",
                source_audit_run_id=9,
                evidence_kind="test_missing",
                evidence_verified_at="2026-08-13T00:00:00Z",
            )
        ]
    )
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "20",
                "ASSIGNED_BY_ID": "1",
                "DATE_CREATE": "2026-08-10T10:00:00Z",
                "DATE_MODIFY": "2026-08-10T11:00:00Z",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "SOURCE_ID": "WEB",
            },
            {"ID": "21", "STATUS_ID": "NEW", "STATUS_SEMANTIC_ID": "P"},
        ],
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "30",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "20",
                "RESPONSIBLE_ID": "1",
                "TYPE_ID": "2",
                "DIRECTION": "2",
                "COMPLETED": "Y",
                "END_TIME": "2026-08-10T10:10:00Z",
            },
            {
                "ID": "31",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "11",
                "TYPE_ID": "5",
                "COMPLETED": "N",
            },
        ],
    )
    await store.upsert_entities(
        "deal_stage_history",
        [
            {
                "ID": "40",
                "OWNER_ID": "10",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
                "CREATED_TIME": "2026-08-10T10:00:00Z",
            }
        ],
    )
    await store.upsert_entities(
        "lead_stage_history",
        [
            {
                "ID": "41",
                "OWNER_ID": "20",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "CREATED_TIME": "2026-08-10T10:00:00Z",
            }
        ],
    )

    report = await build_fact_quality_report(database_path)
    coverage = {item.key: item for item in report.coverages}

    assert report.deal_count == 2
    assert report.lead_count == 2
    assert report.sales_activity_count == 2
    assert report.manager_ids_observed == 2
    assert report.manager_ids_mapped == 1
    assert coverage["deal.created_at"].covered == 1
    assert coverage["deal.created_at"].total == 2
    assert coverage["deal.stage_history_owner_match"].covered == 1
    assert coverage["lead.created_at"].covered == 1
    assert coverage["lead.stage_history_owner_match"].covered == 1
    assert coverage["sales_activity.responsible_user_id"].covered == 1
    assert coverage["sales_activity.observed_timestamp"].covered == 1
    assert coverage["observed_manager_id.directory_mapping"].covered == 1


@pytest.mark.asyncio
async def test_fact_quality_formatter_has_no_quality_threshold_verdict(tmp_path) -> None:
    database_path = str(tmp_path / "quality.db")
    await CrmStore(database_path).initialize()
    report = await build_fact_quality_report(database_path)
    text = format_fact_quality_for_ai(report)

    assert "DESCRIPTIVE, NO PASS/FAIL THRESHOLD" in text
    assert "Do not invent a minimum acceptable coverage percentage" in text
    assert "Business-policy readiness is separate" in text
    assert "No CRM write" in text
