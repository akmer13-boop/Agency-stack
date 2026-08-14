from __future__ import annotations

import pytest

from app.services.rop_data_gap_diagnostics import (
    build_data_gap_diagnostics,
    format_data_gap_diagnostics_for_ai,
)
from app.storage.crm_store import CrmStore, CrmTombstone


@pytest.mark.asyncio
async def test_data_gap_diagnostics_returns_exact_active_ids(tmp_path) -> None:
    database_path = str(tmp_path / "gaps.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "user",
        [{"ID": "1", "NAME": "Anna", "ACTIVE": True}],
    )
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
            {
                "ID": "21",
                "ASSIGNED_BY_ID": "2",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
            },
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
                "RESPONSIBLE_ID": "2",
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

    report = await build_data_gap_diagnostics(database_path)
    gaps = {item.key: item for item in report.gaps}

    assert gaps["lead.source_id"].entity_ids == ("21",)
    assert gaps["deal.created_at"].entity_ids == ("11",)
    assert gaps["deal.stage_history_owner_match"].entity_ids == ("11",)
    assert gaps["lead.stage_history_owner_match"].entity_ids == ("21",)
    assert gaps["sales_activity.observed_timestamp"].entity_ids == ("31",)
    assert gaps["observed_actor_id.resolution"].entity_ids == ("2",)

    assert len(report.unresolved_actors) == 1
    actor = report.unresolved_actors[0]
    assert actor.actor_id == "2"
    assert actor.deal_references == 1
    assert actor.lead_references == 1
    assert actor.activity_references == 1
    assert actor.total_references == 3

    assert "12" not in {entity_id for gap in report.gaps for entity_id in gap.entity_ids}


@pytest.mark.asyncio
async def test_data_gap_formatter_is_diagnostic_only(tmp_path) -> None:
    database_path = str(tmp_path / "empty.db")
    await CrmStore(database_path).initialize()

    report = await build_data_gap_diagnostics(database_path)
    text = format_data_gap_diagnostics_for_ai(report)

    assert "EXACT CURRENT DATA GAPS" in text
    assert "Do not infer the business importance" in text
    assert "Do not call an unresolved actor deleted" in text
    assert "no crm write" in text.lower()
