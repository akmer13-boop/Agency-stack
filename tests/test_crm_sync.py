from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.integrations.bitrix24.sync_client import SyncBitrix24Client
from app.services.bitrix24_sync import get_bitrix_sync_status, run_initial_bitrix_sync
from app.storage.crm_store import CrmStore

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


@pytest.mark.asyncio
async def test_crm_store_upserts_entities_idempotently(tmp_path: Path) -> None:
    store = CrmStore(str(tmp_path / "agency.db"))
    await store.initialize()
    run_id = await store.start_run()

    assert await store.upsert_entities(
        "deal",
        [{"ID": "1", "TITLE": "First", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}],
        modified_field="DATE_MODIFY",
    ) == 1
    assert await store.upsert_entities(
        "deal",
        [{"ID": "1", "TITLE": "Updated", "DATE_MODIFY": "2026-08-07T11:00:00+03:00"}],
        modified_field="DATE_MODIFY",
    ) == 1

    counts = await store.count_by_type()
    assert counts == {"deal": 1}

    await store.finish_run(run_id, counts)
    status = await store.get_last_run()
    assert status.status == "completed"
    assert status.summary == {"deal": 1}


@pytest.mark.asyncio
async def test_sync_client_reads_stage_history_container() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/crm.stagehistory.list.json")
        return httpx.Response(
            200,
            json={
                "result": {
                    "items": [
                        {
                            "ID": "11",
                            "OWNER_ID": "77",
                            "STAGE_ID": "NEW",
                            "CREATED_TIME": "2026-08-07T10:00:00+03:00",
                        }
                    ]
                }
            },
        )

    client = SyncBitrix24Client(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )
    items = await client.list_sync_stage_history(entity_type_id=2, max_items=100)
    assert items[0]["ID"] == "11"


@pytest.mark.asyncio
async def test_initial_sync_persists_all_core_entity_types(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeClient:
        async def list_sync_deals(self, *, max_items: int):
            return [{"ID": "1", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def list_sync_leads(self, *, max_items: int):
            return [{"ID": "2", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def list_sync_contacts(self, *, max_items: int):
            return [{"ID": "3", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def list_sync_companies(self, *, max_items: int):
            return [{"ID": "4", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def list_sync_activities(self, *, max_items: int):
            return [{"ID": "5", "LAST_UPDATED": "2026-08-07T10:00:00+03:00"}]

        async def list_sync_stage_history(self, *, entity_type_id: int, max_items: int):
            return [
                {
                    "ID": str(100 + entity_type_id),
                    "OWNER_ID": "1",
                    "CREATED_TIME": "2026-08-07T10:00:00+03:00",
                }
            ]

    monkeypatch.setattr(
        "app.services.bitrix24_sync.build_sync_client",
        lambda _settings: FakeClient(),
    )
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK_URL,
        database_path=str(tmp_path / "sync.db"),
        bitrix24_sync_max_items_per_entity=100,
    )

    result = await run_initial_bitrix_sync(settings)
    assert result.counts == {
        "deal": 1,
        "lead": 1,
        "contact": 1,
        "company": 1,
        "activity": 1,
        "deal_stage_history": 1,
        "lead_stage_history": 1,
    }

    status, counts = await get_bitrix_sync_status(settings)
    assert status.status == "completed"
    assert counts == result.counts
