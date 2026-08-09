from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_deal import build_deal_drilldown
from app.services.rop_deal_evidence import (
    build_deal_stage_evidence,
    format_deal_stage_evidence,
    format_deal_stage_evidence_for_ai,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_deal_stage_evidence_counts_only_activity_after_current_stage(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities("department", [{"ID": "8", "NAME": "Продажи B2B"}])
    await store.upsert_entities(
        "user",
        [
            {
                "ID": "2320",
                "NAME": "Виктория",
                "LAST_NAME": "Полякова",
                "ACTIVE": True,
                "UF_DEPARTMENT": [8],
            }
        ],
    )
    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "7040",
                "TITLE": "Evidence test",
                "CATEGORY_ID": "8",
                "STAGE_ID": "C8:PREPAYMENT_INVOICE",
                "STAGE_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "2320",
                "OPPORTUNITY": "6000000",
                "CURRENCY_ID": "RUB",
                "DATE_CREATE": (now - timedelta(days=80)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=40)).isoformat(),
                "MOVED_TIME": (now - timedelta(days=40)).isoformat(),
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
                "STAGE_ID": "C8:PREPARATION",
                "CREATED_TIME": (now - timedelta(days=60)).isoformat(),
            },
            {
                "ID": "2",
                "OWNER_ID": "7040",
                "STAGE_ID": "C8:PREPAYMENT_INVOICE",
                "CREATED_TIME": (now - timedelta(days=40)).isoformat(),
            },
        ],
        modified_field="CREATED_TIME",
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "401",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "4",
                "SUBJECT": "Email before quote stage",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=50)).isoformat(),
                "RESPONSIBLE_ID": "2320",
                "LAST_UPDATED": (now - timedelta(days=50)).isoformat(),
            },
            {
                "ID": "402",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "4",
                "SUBJECT": "Email after quote stage",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=7)).isoformat(),
                "RESPONSIBLE_ID": "2320",
                "LAST_UPDATED": (now - timedelta(days=7)).isoformat(),
            },
            {
                "ID": "403",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "2",
                "SUBJECT": "Call after quote stage",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=5)).isoformat(),
                "RESPONSIBLE_ID": "2320",
                "LAST_UPDATED": (now - timedelta(days=5)).isoformat(),
            },
            {
                "ID": "404",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "3",
                "SUBJECT": "Internal task after quote stage",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=2)).isoformat(),
                "RESPONSIBLE_ID": "2320",
                "LAST_UPDATED": (now - timedelta(days=2)).isoformat(),
            },
            {
                "ID": "999",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "9999",
                "TYPE_ID": "4",
                "COMPLETED": "Y",
                "END_TIME": now.isoformat(),
            },
        ],
        modified_field="LAST_UPDATED",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_deal_drilldown(settings, 7040, now=now)
    assert report is not None
    assert report.activities_count == 4
    assert report.sla_severity == "critical"

    evidence = await build_deal_stage_evidence(settings, report, now=now)
    assert evidence.stage_entered_at == now - timedelta(days=40)
    assert evidence.activities_after_stage == 3
    assert evidence.completed_after_stage == 3
    assert evidence.completed_communications_after_stage == 2
    assert dict(evidence.activity_type_counts) == {
        "E-mail": 1,
        "Задача": 1,
        "Звонок": 1,
    }
    assert evidence.last_activity_type == "Задача"
    assert evidence.days_since_last_activity == 2
    assert evidence.last_communication_type == "Звонок"
    assert evidence.days_since_last_communication == 5
    assert evidence.next_open_activity_exists is False

    text = format_deal_stage_evidence(report, evidence, timezone_name="UTC")
    assert "CRM-активностей после входа: 3" in text
    assert "Завершённых коммуникаций после входа: 2" in text
    assert "Дней с последней активности: 2" in text
    assert "Незавершённого следующего шага в CRM сейчас нет" in text

    ai_text = format_deal_stage_evidence_for_ai(report, evidence, timezone_name="UTC")
    assert "EVIDENCE текущей стадии сделки #7040" in ai_text
    assert "Follow-up после КП" in ai_text
    assert "НЕ доказывает, что follow-up не выполнялся" in ai_text
    assert "Email after quote stage" not in ai_text
    assert "Call after quote stage" not in ai_text
    assert "Internal task after quote stage" not in ai_text
