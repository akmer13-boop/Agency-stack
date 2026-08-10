from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_deal import (
    build_deal_drilldown,
    format_deal_drilldown,
    format_deal_for_ai,
)
from app.storage.crm_store import CrmStore

WEBHOOK = "https://b24.example.test/rest/7/supersecretcode/"


@pytest.mark.asyncio
async def test_deal_drilldown_contains_secret_free_bitrix_url(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "7040",
                "TITLE": "URL test deal",
                "CATEGORY_ID": "8",
                "STAGE_ID": "C8:NEW",
                "STAGE_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "2320",
                "OPPORTUNITY": "1000",
                "CURRENCY_ID": "RUB",
                "DATE_CREATE": (now - timedelta(days=2)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=1)).isoformat(),
                "MOVED_TIME": (now - timedelta(days=1)).isoformat(),
            }
        ],
        modified_field="DATE_MODIFY",
    )

    settings = Settings(
        _env_file=None,
        database_path=database_path,
        bitrix24_webhook_url=WEBHOOK,
    )
    report = await build_deal_drilldown(settings, 7040, now=now)

    assert report is not None
    assert report.bitrix_url == "https://b24.example.test/crm/deal/details/7040/"
    assert "supersecretcode" not in report.bitrix_url
    assert "/rest/" not in report.bitrix_url

    text = format_deal_drilldown(report, timezone_name="UTC", now=now)
    assert "https://b24.example.test/crm/deal/details/7040/" in text
    assert "supersecretcode" not in text

    ai_text = format_deal_for_ai(report, timezone_name="UTC")
    assert "https://b24.example.test/crm/deal/details/7040/" in ai_text
    assert "supersecretcode" not in ai_text


@pytest.mark.asyncio
async def test_deal_drilldown_without_bitrix_config_has_no_url(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "1",
                "CATEGORY_ID": "0",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
                "DATE_CREATE": now.isoformat(),
            }
        ],
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_deal_drilldown(settings, 1, now=now)

    assert report is not None
    assert report.bitrix_url is None
    assert "ссылка недоступна" in format_deal_drilldown(
        report,
        timezone_name="UTC",
        now=now,
    )
