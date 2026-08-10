from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_directory import load_rop_directory
from app.services.rop_leads import build_lead_intelligence
from app.services.rop_weekend_leads import (
    build_weekend_lead_report,
    format_weekend_lead_report,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_weekend_report_separates_activity_evidence(tmp_path: Path) -> None:
    now = datetime(2026, 8, 10, 10, 30, tzinfo=UTC)
    database_path = str(tmp_path / "weekend.db")
    store = CrmStore(database_path)
    await store.initialize()

    created = datetime(2026, 8, 8, 7, 0, tzinfo=UTC)
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "1",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": created.isoformat(),
            },
            {
                "ID": "2",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": (created + timedelta(hours=1)).isoformat(),
            },
            {
                "ID": "3",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "11",
                "DATE_CREATE": (created + timedelta(hours=2)).isoformat(),
            },
            {
                "ID": "4",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "11",
                "DATE_CREATE": (created + timedelta(hours=3)).isoformat(),
            },
        ],
    )

    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "101",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 2,
                "DIRECTION": 2,
                "COMPLETED": "Y",
                "END_TIME": (created + timedelta(minutes=30)).isoformat(),
            },
            {
                "ID": "102",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "2",
                "TYPE_ID": 2,
                "DIRECTION": 1,
                "COMPLETED": "Y",
                "END_TIME": (created + timedelta(hours=1, minutes=30)).isoformat(),
            },
            {
                "ID": "103",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "3",
                "TYPE_ID": 3,
                "AUTOCOMPLETE_RULE": 1,
                "COMPLETED": "Y",
                "END_TIME": (created + timedelta(hours=3)).isoformat(),
            },
            {
                "ID": "104",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "4",
                "TYPE_ID": 3,
                "COMPLETED": "N",
                "LAST_UPDATED": (created + timedelta(hours=4)).isoformat(),
            },
            {
                "ID": "105",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "4",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (created + timedelta(hours=5)).isoformat(),
            },
        ],
        modified_field="LAST_UPDATED",
    )

    settings = Settings(
        _env_file=None,
        database_path=database_path,
        rop_timezone="Europe/Moscow",
    )
    report = await build_weekend_lead_report(settings, now=now)

    assert report.total_leads == 4
    assert report.leads_with_activity == 4
    assert report.leads_with_communication == 2
    assert report.leads_with_manager_evidence == 2
    assert report.manager_evidence_events == 2
    assert report.leads_with_system_activity == 1
    assert report.leads_with_unknown_activity == 1
    assert report.completed_human_actions == 1
    assert report.system_activities == 1
    assert report.unknown_activities == 1

    directory = await load_rop_directory(database_path)
    text = format_weekend_lead_report(
        report,
        directory,
        timezone_name=settings.rop_timezone,
    )
    assert "evidence действия со стороны менеджера" in text
    assert "автозавершённой системной активностью" in text
    assert "неклассифицированной активностью" in text


@pytest.mark.asyncio
async def test_lead_intelligence_classification_preserves_communications(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    database_path = str(tmp_path / "leads.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "201",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 4,
                "DIRECTION": 2,
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "ID": "202",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 4,
                "DIRECTION": 1,
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "ID": "203",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(hours=3)).isoformat(),
            },
            {
                "ID": "204",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 3,
                "AUTOCOMPLETE_RULE": 1,
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(hours=4)).isoformat(),
            },
            {
                "ID": "205",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 3,
                "COMPLETED": "N",
                "LAST_UPDATED": (now - timedelta(hours=5)).isoformat(),
            },
        ],
        modified_field="LAST_UPDATED",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_lead_intelligence(settings, 7, now=now)

    assert report.crm_activities == 5
    assert report.completed_communications == 2
    assert report.manager_evidence_events == 2
    assert report.human_actions == 1
    assert report.system_activities == 1
    assert report.unknown_activities == 1
