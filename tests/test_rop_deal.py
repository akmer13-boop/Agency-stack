import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.services.rop_deal import (
    DealDetailBitrix24Client,
    build_deal_drilldown,
    format_deal_drilldown,
    format_deal_for_ai,
)
from app.storage.crm_store import CrmStore

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


@pytest.mark.asyncio
async def test_deal_drilldown_uses_local_deal_activities_history_and_identity(
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
                "TITLE": "Test B2B deal",
                "CATEGORY_ID": "8",
                "STAGE_ID": "C8:PREPAYMENT_INVOICE",
                "STAGE_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "2320",
                "OPPORTUNITY": "6000000",
                "CURRENCY_ID": "RUB",
                "DATE_CREATE": (now - timedelta(days=120)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=88)).isoformat(),
                "MOVED_TIME": (now - timedelta(days=88)).isoformat(),
            }
        ],
        modified_field="DATE_MODIFY",
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "501",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "2",
                "SUBJECT": "Последний звонок клиенту",
                "COMPLETED": "Y",
                "END_TIME": (now - timedelta(days=7)).isoformat(),
                "RESPONSIBLE_ID": "2320",
                "LAST_UPDATED": (now - timedelta(days=7)).isoformat(),
            },
            {
                "ID": "502",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "7040",
                "TYPE_ID": "3",
                "SUBJECT": "Follow-up по КП",
                "COMPLETED": "N",
                "DEADLINE": (now - timedelta(days=2)).isoformat(),
                "RESPONSIBLE_ID": "2320",
                "LAST_UPDATED": (now - timedelta(days=3)).isoformat(),
            },
            {
                "ID": "999",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "9999",
                "TYPE_ID": "3",
                "SUBJECT": "Other deal",
                "COMPLETED": "N",
            },
        ],
        modified_field="LAST_UPDATED",
    )
    await store.upsert_entities(
        "deal_stage_history",
        [
            {
                "ID": "1",
                "OWNER_ID": "7040",
                "STAGE_ID": "C8:PREPARATION",
                "CREATED_TIME": (now - timedelta(days=100)).isoformat(),
            },
            {
                "ID": "2",
                "OWNER_ID": "7040",
                "STAGE_ID": "C8:PREPAYMENT_INVOICE",
                "CREATED_TIME": (now - timedelta(days=88)).isoformat(),
            },
        ],
        modified_field="CREATED_TIME",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_deal_drilldown(settings, 7040, now=now)

    assert report is not None
    assert report.deal_id == "7040"
    assert report.opportunity == 6_000_000
    assert report.stage_age_days == 88
    assert report.sla_severity == "critical"
    assert report.sla_rule_label == "Follow-up после КП"
    assert report.activities_count == 2
    assert report.last_completed_activity is not None
    assert report.last_completed_activity.activity_type.startswith("Звонок")
    assert report.next_open_activity is not None
    assert report.next_open_activity.activity_type.startswith("Задача")
    assert [item.stage_id for item in report.stage_history] == [
        "C8:PREPARATION",
        "C8:PREPAYMENT_INVOICE",
    ]

    text = format_deal_drilldown(report, timezone_name="UTC", now=now)
    assert "ИИ-РОП · сделка #7040" in text
    assert "6 000 000.00 RUB" in text
    assert "Виктория Полякова · Продажи B2B" in text
    assert "КРИТИЧНО · Follow-up после КП" in text
    assert "Последний звонок клиенту" in text
    assert "просрочка 2 дн." in text

    ai_text = format_deal_for_ai(report, timezone_name="UTC")
    assert "Последняя завершённая активность: Звонок" in ai_text
    assert "Последний звонок клиенту" not in ai_text
    assert "тексты комментариев" in ai_text.lower()


@pytest.mark.asyncio
async def test_deal_timeline_client_reads_only_requested_deal() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/crm.timeline.comment.list.json")
        payload = json.loads(request.content)
        assert payload["filter"] == {"ENTITY_ID": 7040, "ENTITY_TYPE": "deal"}
        assert payload["order"] == {"CREATED": "DESC"}
        assert payload["start"] == 0
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "ID": "77",
                        "CREATED": "2026-08-08T10:00:00+03:00",
                        "ENTITY_ID": "7040",
                        "ENTITY_TYPE": "deal",
                        "AUTHOR_ID": "2320",
                        "COMMENT": "Клиент попросил вернуться завтра",
                    }
                ]
            },
        )

    client = DealDetailBitrix24Client(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )
    comments = await client.list_deal_timeline_comments("7040", max_items=5)

    assert len(comments) == 1
    assert comments[0]["ENTITY_ID"] == "7040"
    assert comments[0]["COMMENT"] == "Клиент попросил вернуться завтра"
