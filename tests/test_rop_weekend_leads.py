from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_directory import load_rop_directory
from app.services.rop_weekend_leads import (
    build_weekend_lead_report,
    format_weekend_lead_report,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_weekend_lead_report_uses_calendar_weekend_and_manager_evidence(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 10, 10, 30, tzinfo=UTC)  # Monday 13:30 Europe/Moscow
    database_path = str(tmp_path / "weekend.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "department",
        [
            {"ID": "7", "NAME": "Продажи B2C"},
            {"ID": "8", "NAME": "Колл-Центр"},
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

    sat_created = datetime(2026, 8, 8, 7, 0, tzinfo=UTC)  # 10:00 Moscow
    sun_created = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)  # 12:00 Moscow
    sun_failed_created = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)

    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "1",
                "TITLE": "SECRET WEEKEND LEAD ONE",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": sat_created.isoformat(),
                "DATE_MODIFY": sat_created.isoformat(),
            },
            {
                "ID": "2",
                "TITLE": "SECRET WEEKEND LEAD TWO",
                "STATUS_SEMANTIC_ID": "S",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": sun_created.isoformat(),
                "DATE_MODIFY": sun_created.isoformat(),
            },
            {
                "ID": "3",
                "TITLE": "SECRET WEEKEND LEAD THREE",
                "STATUS_SEMANTIC_ID": "F",
                "ASSIGNED_BY_ID": "11",
                "DATE_CREATE": sun_failed_created.isoformat(),
                "DATE_MODIFY": sun_failed_created.isoformat(),
            },
            {
                "ID": "4",
                "TITLE": "MONDAY OUTSIDE",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": datetime(2026, 8, 10, 6, 0, tzinfo=UTC).isoformat(),
                "DATE_MODIFY": datetime(2026, 8, 10, 6, 0, tzinfo=UTC).isoformat(),
            },
        ],
        modified_field="DATE_MODIFY",
    )

    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "101",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 2,
                "COMPLETED": "Y",
                "END_TIME": (sat_created + timedelta(hours=1)).isoformat(),
                "LAST_UPDATED": (sat_created + timedelta(hours=1)).isoformat(),
            },
            {
                "ID": "102",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (sat_created + timedelta(hours=2)).isoformat(),
                "LAST_UPDATED": (sat_created + timedelta(hours=2)).isoformat(),
            },
            {
                "ID": "103",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "2",
                "TYPE_ID": 3,
                "COMPLETED": "N",
                "LAST_UPDATED": (sun_created + timedelta(hours=1)).isoformat(),
            },
            {
                "ID": "104",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "3",
                "TYPE_ID": 4,
                "COMPLETED": "Y",
                "END_TIME": (sun_failed_created + timedelta(hours=2)).isoformat(),
                "LAST_UPDATED": (sun_failed_created + timedelta(hours=2)).isoformat(),
            },
            {
                "ID": "105",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "2",
                "TYPE_ID": 2,
                "COMPLETED": "Y",
                "END_TIME": (sun_created - timedelta(hours=1)).isoformat(),
                "LAST_UPDATED": (sun_created - timedelta(hours=1)).isoformat(),
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

    assert report.start_at == datetime(2026, 8, 7, 21, 0, tzinfo=UTC)
    assert report.end_at == datetime(2026, 8, 9, 21, 0, tzinfo=UTC)
    assert report.total_leads == 3
    assert report.leads_with_activity == 3
    assert report.leads_with_communication == 2
    assert report.completed_communications == 2
    assert report.current_active == 1
    assert report.current_success == 1
    assert report.current_failed == 1
    assert report.median_first_communication_seconds == 5400.0

    by_manager = {item.assigned_by_id: item for item in report.managers}
    assert by_manager["10"].leads == 2
    assert by_manager["10"].leads_with_activity == 2
    assert by_manager["10"].leads_with_communication == 1
    assert by_manager["10"].completed_communications == 1
    assert by_manager["11"].leads == 1
    assert by_manager["11"].leads_with_communication == 1

    directory = await load_rop_directory(database_path)
    text = format_weekend_lead_report(
        report,
        directory,
        timezone_name=settings.rop_timezone,
    )
    assert "2026-08-08 00:00 — 2026-08-10 00:00" in text
    assert "Иван Петров · Продажи B2C (ID 10)" in text
    assert "Анна Смирнова · Колл-Центр (ID 11)" in text
    assert "1 ч 30 мин" in text
    assert "а не first-response SLA" in text
    assert "SECRET WEEKEND" not in text


@pytest.mark.asyncio
async def test_weekend_report_excludes_non_directory_attribution_from_manager_rows(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 10, 10, 30, tzinfo=UTC)
    database_path = str(tmp_path / "human_scope_weekend.db")
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

    sat = datetime(2026, 8, 8, 7, 0, tzinfo=UTC)
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "1",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "10",
                "DATE_CREATE": sat.isoformat(),
                "DATE_MODIFY": sat.isoformat(),
            },
            {
                "ID": "2",
                "STATUS_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "7912",
                "DATE_CREATE": (sat + timedelta(hours=1)).isoformat(),
                "DATE_MODIFY": (sat + timedelta(hours=1)).isoformat(),
            },
        ],
        modified_field="DATE_MODIFY",
    )

    settings = Settings(
        _env_file=None,
        database_path=database_path,
        rop_timezone="Europe/Moscow",
    )
    report = await build_weekend_lead_report(settings, now=now)

    assert report.total_leads == 2
    assert [item.assigned_by_id for item in report.managers] == ["10"]
    assert [item.assigned_by_id for item in report.excluded_attribution] == ["7912"]
    assert sum(item.leads for item in report.managers + report.excluded_attribution) == 2

    directory = await load_rop_directory(database_path)
    text = format_weekend_lead_report(
        report,
        directory,
        timezone_name=settings.rop_timezone,
    )
    assert "Иван Петров · Продажи (ID 10)" in text
    assert "Исключённая атрибуция · НЕ менеджеры:" in text
    assert "ID 7912" in text
