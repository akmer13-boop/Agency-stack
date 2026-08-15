from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_directory import load_rop_directory
from app.services.rop_leads import (
    build_lead_intelligence,
    format_lead_intelligence,
    format_lead_intelligence_for_ai,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_lead_intelligence_builds_grounded_rolling_window(tmp_path: Path) -> None:
    now = datetime(2026, 8, 9, 20, 0, tzinfo=UTC)
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "department",
        [
            {"ID": "7", "NAME": "Продажи B2C"},
            {"ID": "8", "NAME": "Продажи B2B"},
        ],
    )
    await store.upsert_entities(
        "user",
        [
            {
                "ID": "10",
                "NAME": "Иван",
                "LAST_NAME": "Петров",
                "ACTIVE": True,
                "UF_DEPARTMENT": [7],
            },
            {
                "ID": "11",
                "NAME": "Анна",
                "LAST_NAME": "Смирнова",
                "ACTIVE": True,
                "UF_DEPARTMENT": [8],
            },
        ],
    )

    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "1",
                "TITLE": "SECRET LEAD ONE",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "SOURCE_ID": "WEB",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": (now - timedelta(days=2)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=1)).isoformat(),
            },
            {
                "ID": "2",
                "TITLE": "SECRET LEAD TWO",
                "STATUS_ID": "IN_PROCESS",
                "STATUS_SEMANTIC_ID": "P",
                "SOURCE_ID": "CALL",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": (now - timedelta(days=10)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=4)).isoformat(),
            },
            {
                "ID": "3",
                "TITLE": "SECRET WON LEAD",
                "STATUS_ID": "CONVERTED",
                "STATUS_SEMANTIC_ID": "S",
                "SOURCE_ID": "REFERRAL",
                "ASSIGNED_BY_ID": "11",
                "DATE_CREATE": (now - timedelta(days=6)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=1)).isoformat(),
            },
            {
                "ID": "4",
                "TITLE": "SECRET LOST LEAD",
                "STATUS_ID": "JUNK",
                "STATUS_SEMANTIC_ID": "F",
                "SOURCE_ID": "WEB",
                "ASSIGNED_BY_ID": "11",
                "DATE_CREATE": (now - timedelta(days=5)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=1)).isoformat(),
            },
            {
                "ID": "5",
                "TITLE": "SECRET OLD LEAD",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "SOURCE_ID": "WEB",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": (now - timedelta(days=20)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=6)).isoformat(),
            },
        ],
        modified_field="DATE_MODIFY",
    )

    await store.upsert_entities(
        "lead_stage_history",
        [
            {
                "ID": "101",
                "OWNER_ID": "1",
                "CREATED_TIME": (now - timedelta(days=2)).isoformat(),
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
            },
            {
                "ID": "102",
                "OWNER_ID": "2",
                "CREATED_TIME": (now - timedelta(days=4)).isoformat(),
                "STATUS_ID": "IN_PROCESS",
                "STATUS_SEMANTIC_ID": "P",
            },
            {
                "ID": "103",
                "OWNER_ID": "3",
                "CREATED_TIME": (now - timedelta(days=1)).isoformat(),
                "STATUS_ID": "CONVERTED",
                "STATUS_SEMANTIC_ID": "S",
            },
            {
                "ID": "104",
                "OWNER_ID": "4",
                "CREATED_TIME": (now - timedelta(days=1)).isoformat(),
                "STATUS_ID": "JUNK",
                "STATUS_SEMANTIC_ID": "F",
            },
        ],
        modified_field="CREATED_TIME",
    )

    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "201",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 4,
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=1)).isoformat(),
            },
            {
                "ID": "202",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "2",
                "TYPE_ID": 2,
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=2)).isoformat(),
            },
            {
                "ID": "203",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "5",
                "TYPE_ID": 3,
                "COMPLETED": "N",
                "LAST_UPDATED": (now - timedelta(days=1)).isoformat(),
            },
            {
                "ID": "204",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "5",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=1)).isoformat(),
            },
        ],
        modified_field="LAST_UPDATED",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_lead_intelligence(settings, 7, now=now)

    assert report.new_leads == 3
    assert report.current_active == 3
    assert report.current_success == 1
    assert report.current_failed == 1
    assert report.leads_with_status_events == 4
    assert report.status_events == 4
    assert report.successful_finalizations == 1
    assert report.failed_finalizations == 1
    assert report.history_schema_ready is True
    assert ("CONVERTED", "S", 1) in {
        (item.status_id, item.semantic, item.count) for item in report.final_statuses
    }
    assert ("JUNK", "F", 1) in {
        (item.status_id, item.semantic, item.count) for item in report.final_statuses
    }
    assert report.active_attention_3d == 2
    assert report.active_critical_5d == 1
    assert report.crm_activities == 4
    assert report.completed_activities == 3
    assert report.completed_communications == 2
    assert ("E-mail", 1) in report.communication_type_counts
    assert ("Звонок", 1) in report.communication_type_counts
    assert ("Пользовательское действие", 1) in report.activity_type_counts
    assert ("Пользовательское действие", 1) not in report.communication_type_counts

    directory = await load_rop_directory(database_path)
    text = format_lead_intelligence(report, directory)
    assert "Иван Петров · Продажи B2C (ID 10)" in text
    assert "Анна Смирнова · Продажи B2B (ID 11)" in text
    assert "Доля успешных среди финализированных переходов: 50.0% (n=2)" in text
    assert "Финализации по статусам за окно:" in text
    assert "не lead→deal cohort conversion" in text

    ai_text = format_lead_intelligence_for_ai(report, directory)
    assert "SECRET LEAD" not in ai_text
    assert "lead→deal conversion" in ai_text
    assert "Не называй менеджера худшим без явной метрики" in ai_text
    assert "не обещай выгрузить список" in ai_text


@pytest.mark.asyncio
async def test_lead_intelligence_marks_legacy_history_unreliable(tmp_path: Path) -> None:
    now = datetime(2026, 8, 9, 20, 0, tzinfo=UTC)
    database_path = str(tmp_path / "legacy.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "1",
                "STATUS_ID": "CONVERTED",
                "STATUS_SEMANTIC_ID": "S",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": (now - timedelta(days=2)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=1)).isoformat(),
            }
        ],
        modified_field="DATE_MODIFY",
    )
    await store.upsert_entities(
        "lead_stage_history",
        [
            {
                "ID": "101",
                "OWNER_ID": "1",
                "CREATED_TIME": (now - timedelta(days=1)).isoformat(),
                "STAGE_ID": "CONVERTED",
                "STAGE_SEMANTIC_ID": "S",
            }
        ],
        modified_field="CREATED_TIME",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_lead_intelligence(settings, 7, now=now)
    assert report.history_schema_ready is False

    directory = await load_rop_directory(database_path)
    text = format_lead_intelligence(report, directory)
    assert "временно не считаются достоверными" in text
    assert "/bitrix_sync_incremental" in text


@pytest.mark.asyncio
async def test_lead_intelligence_excludes_non_directory_attribution_from_manager_rows(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    database_path = str(tmp_path / "human_scope_leads.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "department",
        [{"ID": "7", "NAME": "Продажи"}],
    )
    await store.upsert_entities(
        "user",
        [
            {
                "ID": "10",
                "NAME": "Иван",
                "LAST_NAME": "Петров",
                "ACTIVE": True,
                "UF_DEPARTMENT": [7],
            }
        ],
    )
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "1",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "SOURCE_ID": "WEB",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": (now - timedelta(days=1)).isoformat(),
                "DATE_MODIFY": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "ID": "2",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "SOURCE_ID": "OPENLINE",
                "ASSIGNED_BY_ID": "7912",
                "DATE_CREATE": (now - timedelta(days=1)).isoformat(),
                "DATE_MODIFY": (now - timedelta(hours=3)).isoformat(),
            },
            {
                "ID": "3",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "SOURCE_ID": "OTHER",
                "ASSIGNED_BY_ID": "484",
                "DATE_CREATE": (now - timedelta(days=1)).isoformat(),
                "DATE_MODIFY": (now - timedelta(hours=4)).isoformat(),
            },
        ],
        modified_field="DATE_MODIFY",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_lead_intelligence(settings, 7, now=now)

    assert report.total_leads == 3
    assert report.new_leads == 3
    assert report.current_active == 3

    assert [item.assigned_by_id for item in report.managers] == ["10"]
    excluded_ids = {item.assigned_by_id for item in report.excluded_attribution}
    assert excluded_ids == {"484", "7912"}

    combined = report.managers + report.excluded_attribution
    assert sum(item.new_leads for item in combined) == report.new_leads
    assert sum(item.current_active for item in combined) == report.current_active

    directory = await load_rop_directory(database_path)
    text = format_lead_intelligence(report, directory)
    assert "Иван Петров · Продажи (ID 10)" in text
    assert "Исключённая атрибуция · НЕ менеджеры:" in text
    assert "ID 7912" in text
    assert "ID 484" in text
