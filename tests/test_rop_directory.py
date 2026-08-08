from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.integrations.bitrix24.client import Bitrix24ReadOnlyClient
from app.services.rop_daily import build_rop_daily
from app.services.rop_directory import enrich_responsible_ids, load_rop_directory
from app.storage.crm_store import CrmStore

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


@pytest.mark.asyncio
async def test_bitrix_directory_client_minimizes_user_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/user.get.json"):
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "ID": "10",
                            "NAME": "Иван",
                            "LAST_NAME": "Петров",
                            "EMAIL": "private@example.test",
                            "ACTIVE": True,
                            "WORK_POSITION": "Менеджер",
                            "UF_DEPARTMENT": [7],
                        }
                    ]
                },
            )
        if request.url.path.endswith("/department.get.json"):
            return httpx.Response(
                200,
                json={
                    "result": [
                        {"ID": "7", "NAME": "B2C", "PARENT": "1", "UF_HEAD": "10"}
                    ]
                },
            )
        raise AssertionError(request.url.path)

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    users = await client.list_users()
    departments = await client.list_departments()

    assert users == [
        {
            "ID": "10",
            "NAME": "Иван",
            "LAST_NAME": "Петров",
            "ACTIVE": True,
            "WORK_POSITION": "Менеджер",
            "UF_DEPARTMENT": [7],
        }
    ]
    assert "EMAIL" not in users[0]
    assert departments == [{"ID": "7", "NAME": "B2C", "PARENT": "1"}]


@pytest.mark.asyncio
async def test_local_directory_enriches_manager_ids(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities("department", [{"ID": "7", "NAME": "B2C"}])
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

    directory = await load_rop_directory(database_path)
    text = enrich_responsible_ids("• ID 10 | активных 5\n• сделка | отв. ID 10 | тест", directory)

    assert "Иван Петров · B2C (ID 10)" in text
    assert "отв. Иван Петров · B2C (ID 10)" in text


@pytest.mark.asyncio
async def test_daily_brief_uses_local_employee_name_and_stage_sla(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities("department", [{"ID": "7", "NAME": "B2C"}])
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
        "deal",
        [
            {
                "ID": "100",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:PREPARATION",
                "STAGE_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "10",
                "OPPORTUNITY": "500000",
                "CURRENCY_ID": "RUB",
                "DATE_CREATE": (now - timedelta(days=10)).isoformat(),
                "DATE_MODIFY": (now - timedelta(days=5)).isoformat(),
                "MOVED_TIME": (now - timedelta(days=5)).isoformat(),
            }
        ],
        modified_field="DATE_MODIFY",
    )

    settings = Settings(_env_file=None, database_path=database_path)
    text = await build_rop_daily(settings)

    assert "ИИ-РОП · Daily Brief" in text
    assert "Иван Петров · B2C" in text
    assert "#100" in text
    assert "Кого разбирать сегодня по stage-specific SLA:" in text
    assert "SLA-критично 1" in text
    assert "5+ дней" not in text
    assert "в LLM для этого отчёта не передаются" in text
