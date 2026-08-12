from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from app.storage.crm_store import CrmStore, CrmTombstone


async def _fetch_one(database_path: str, query: str, params: tuple = ()):
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(query, params)
        return await cursor.fetchone()


@pytest.mark.asyncio
async def test_initialize_creates_tombstone_table_and_active_view(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()

    table = await _fetch_one(
        database_path,
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = 'crm_entity_tombstones'
        """,
    )
    view = await _fetch_one(
        database_path,
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'view' AND name = 'crm_active_entities'
        """,
    )

    assert table == ("crm_entity_tombstones",)
    assert view == ("crm_active_entities",)


@pytest.mark.asyncio
async def test_soft_tombstone_hides_from_active_view_but_preserves_raw_payload(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "deal",
        [{"ID": "8479", "TITLE": "Historical deal"}],
    )

    applied = await store.apply_tombstones(
        [
            CrmTombstone(
                entity_type="deal",
                entity_id="8479",
                source_audit_run_id=8,
                evidence_kind="full_sync_absence+direct_get_not_found",
                evidence_verified_at="2026-08-12T10:00:00+00:00",
            )
        ]
    )
    assert applied == 1

    raw = await _fetch_one(
        database_path,
        """
        SELECT payload_json
        FROM crm_raw_entities
        WHERE entity_type = 'deal' AND entity_id = '8479'
        """,
    )
    active = await _fetch_one(
        database_path,
        """
        SELECT entity_id
        FROM crm_active_entities
        WHERE entity_type = 'deal' AND entity_id = '8479'
        """,
    )

    assert raw is not None
    assert json.loads(raw[0])["TITLE"] == "Historical deal"
    assert active is None


@pytest.mark.asyncio
async def test_upsert_reappearing_entity_automatically_revives_it(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities("contact", [{"ID": "11355", "NAME": "Old"}])
    await store.apply_tombstones(
        [
            CrmTombstone(
                entity_type="contact",
                entity_id="11355",
                source_audit_run_id=8,
                evidence_kind="full_sync_absence+direct_get_not_found",
                evidence_verified_at="2026-08-12T10:00:00+00:00",
            )
        ]
    )
    assert await store.count_tombstones() == {"contact": 1}

    await store.upsert_entities(
        "contact",
        [{"ID": "11355", "NAME": "Returned"}],
    )

    assert await store.count_tombstones() == {}
    active = await _fetch_one(
        database_path,
        """
        SELECT payload_json
        FROM crm_active_entities
        WHERE entity_type = 'contact' AND entity_id = '11355'
        """,
    )
    assert active is not None
    assert json.loads(active[0])["NAME"] == "Returned"


@pytest.mark.asyncio
async def test_tombstone_application_is_idempotent_and_updates_evidence(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities("activity", [{"ID": "99677"}])

    first = CrmTombstone(
        entity_type="activity",
        entity_id="99677",
        source_audit_run_id=8,
        evidence_kind="full_sync_absence+activity_exact_id_list_empty",
        evidence_verified_at="2026-08-12T10:00:00+00:00",
    )
    second = CrmTombstone(
        entity_type="activity",
        entity_id="99677",
        source_audit_run_id=9,
        evidence_kind="reconfirmed_missing",
        evidence_verified_at="2026-08-12T11:00:00+00:00",
    )

    assert await store.apply_tombstones([first]) == 1
    assert await store.apply_tombstones([second]) == 1

    rows = await store.list_tombstones()
    assert len(rows) == 1
    assert rows[0].source_audit_run_id == 9
    assert rows[0].evidence_kind == "reconfirmed_missing"


@pytest.mark.asyncio
async def test_apply_tombstones_fails_closed_when_raw_entity_is_missing(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()

    with pytest.raises(ValueError, match="raw CRM entity does not exist"):
        await store.apply_tombstones(
            [
                CrmTombstone(
                    entity_type="deal",
                    entity_id="999999",
                    source_audit_run_id=8,
                    evidence_kind="test",
                    evidence_verified_at="2026-08-12T10:00:00+00:00",
                )
            ]
        )

    assert await store.count_tombstones() == {}


@pytest.mark.asyncio
async def test_apply_tombstones_rejects_non_current_entity_types(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities("deal_stage_history", [{"ID": "1"}])

    with pytest.raises(ValueError, match="not tombstone-enabled"):
        await store.apply_tombstones(
            [
                CrmTombstone(
                    entity_type="deal_stage_history",
                    entity_id="1",
                    source_audit_run_id=8,
                    evidence_kind="test",
                    evidence_verified_at="2026-08-12T10:00:00+00:00",
                )
            ]
        )


@pytest.mark.asyncio
async def test_stage_history_remains_visible_in_active_view(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "crm.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "deal_stage_history",
        [{"ID": "500", "OWNER_ID": "8479"}],
    )

    row = await _fetch_one(
        database_path,
        """
        SELECT entity_id
        FROM crm_active_entities
        WHERE entity_type = 'deal_stage_history'
          AND entity_id = '500'
        """,
    )
    assert row == ("500",)
