from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_deal import build_deal_drilldown
from app.services.rop_recent_activity import (
    build_recent_deal_activity,
    format_recent_deal_activity,
    format_recent_deal_activity_for_ai,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_recent_activity_counts_exact_window_and_known_communications(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "7040",
                "TITLE": "Recent activity test",
                "CATEGORY_ID": "8",
                "STAGE_ID": "C8:PREPAYMENT_INVOICE",
                "STAGE_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "2320",
                "OPPORTUNITY": "6000000",
                "CURRENCY_ID": "RUB",
                "DATE_CREATE": (now - timedelta(days=120)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=89)).isoformat(),
                "MOVED_TIME": (now - timedelta(days=89)).isoformat(),
            }
        ],
        modified_field="DATE_MODIFY",
    )
    await store.upsert_entities(
        "deal_stage_history",
        [
            {
                "ID": "1",
                "OWNER_ID": "7040",
                "STAGE_ID": "C8:PREPAYMENT_INVOICE",
                "CREATED_TIME": (now - timedelta(days=89)).isoformat(),
            }
        ],
        modified_field="CREATED_TIME",
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "1",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "4",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=3)).isoformat(),
                "LAST_UPDATED": (now - timedelta(days=3)).isoformat(),
            },
            {
                "ID": "2",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "2",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=10)).isoformat(),
                "LAST_UPDATED": (now - timedelta(days=10)).isoformat(),
            },
            {
                "ID": "3",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "6",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=2)).isoformat(),
                "LAST_UPDATED": (now - timedelta(days=2)).isoformat(),
            },
            {
                "ID": "4",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "3",
                "COMPLETED": "N",
                "CREATED": (now - timedelta(days=1)).isoformat(),
                "LAST_UPDATED": (now - timedelta(days=1)).isoformat(),
            },
            {
                "ID": "5",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "4",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=20)).isoformat(),
                "LAST_UPDATED": (now - timedelta(days=20)).isoformat(),
            },
        ],
        modified_field="LAST_UPDATED",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_deal_drilldown(settings, 7040, now=now)
    assert report is not None

    seven = await build_recent_deal_activity(settings, report, 7, now=now)
    assert seven.activities_count == 3
    assert seven.completed_count == 2
    assert seven.open_count == 1
    assert seven.completed_communications_count == 1
    assert dict(seven.activity_type_counts) == {
        "E-mail": 1,
        "Другой тип (ID 6)": 1,
        "Задача": 1,
    }
    assert dict(seven.communication_type_counts) == {"E-mail": 1}
    assert seven.last_activity_type == "Задача"
    assert seven.last_communication_type == "E-mail"
    assert seven.next_open_activity_exists is True

    fourteen = await build_recent_deal_activity(settings, report, 14, now=now)
    assert fourteen.activities_count == 4
    assert fourteen.completed_communications_count == 2
    assert dict(fourteen.communication_type_counts) == {
        "E-mail": 1,
        "Звонок": 1,
    }

    text = format_recent_deal_activity(report, seven, timezone_name="UTC")
    assert "за последние 7 дн." in text
    assert "CRM-активностей: 3" in text
    assert "Подтверждённых коммуникаций: 1" in text
    assert "Другой тип (ID 6) 1" in text
    assert "не считаются коммуникацией" in text

    ai_text = format_recent_deal_activity_for_ai(report, seven, timezone_name="UTC")
    assert "RECENT ACTIVITY сделки #7040" in ai_text
    assert "Completed communications in window: 1" in ai_text
    assert "unknown/other activity types are not communications" in ai_text


@pytest.mark.asyncio
async def test_recent_activity_rejects_unsupported_window(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    settings = Settings(_env_file=None, database_path=database_path)
    report = type("Report", (), {"deal_id": "7040", "next_open_activity": None})()

    with pytest.raises(ValueError, match="between 1 and 365"):
        await build_recent_deal_activity(settings, report, 0)
