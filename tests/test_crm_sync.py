import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.integrations.bitrix24 import Bitrix24RequestError
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

    await store.update_run_progress(run_id, {"deal": 1})
    running_status = await store.get_last_run()
    assert running_status.status == "running"
    assert running_status.summary == {"deal": 1}

    await store.finish_run(run_id, counts)
    status = await store.get_last_run()
    assert status.status == "completed"
    assert status.summary == {"deal": 1}


@pytest.mark.asyncio
async def test_sync_client_reads_stage_history_container() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/crm.stagehistory.list.json")
        payload = json.loads(request.content)
        assert payload["start"] == -1
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
        page_delay_seconds=0,
    )
    items = await client.list_sync_stage_history(entity_type_id=2, max_items=100)
    assert items[0]["ID"] == "11"


@pytest.mark.asyncio
async def test_sync_client_uses_id_cursor_until_real_end() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        after_id = int(payload.get("filter", {}).get(">ID", 0))
        if after_id == 0:
            return httpx.Response(
                200,
                json={"result": [{"ID": str(item_id)} for item_id in range(1, 51)]},
            )
        assert after_id == 50
        return httpx.Response(
            200,
            json={"result": [{"ID": "51"}, {"ID": "52"}]},
        )

    client = SyncBitrix24Client(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
        page_delay_seconds=0,
    )

    items = await client.list_sync_leads()

    assert len(items) == 52
    assert requests[0]["start"] == -1
    assert ">ID" not in requests[0].get("filter", {})
    assert requests[1]["start"] == -1
    assert requests[1]["filter"] == {">ID": 50}


@pytest.mark.asyncio
async def test_sync_client_retries_timeout_without_exposing_webhook() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("slow page", request=request)
        return httpx.Response(200, json={"result": [{"ID": "1"}]})

    client = SyncBitrix24Client(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
        retry_attempts=2,
        retry_backoff_seconds=0,
        page_delay_seconds=0,
    )

    items = await client.list_sync_leads(max_items=10)
    assert items == [{"ID": "1"}]
    assert attempts == 2


@pytest.mark.asyncio
async def test_full_sync_is_unlimited_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.bitrix24_sync_max_items_per_entity == 0
    assert settings.bitrix24_sync_item_limit is None


@pytest.mark.asyncio
async def test_initial_sync_persists_all_core_entity_types(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_limits: list[int | None] = []

    class FakeClient:
        async def iter_sync_deals(self, *, max_items: int | None):
            captured_limits.append(max_items)
            yield [{"ID": "1", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def iter_sync_leads(self, *, max_items: int | None):
            captured_limits.append(max_items)
            yield [{"ID": "2", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def iter_sync_contacts(self, *, max_items: int | None):
            captured_limits.append(max_items)
            yield [{"ID": "3", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def iter_sync_companies(self, *, max_items: int | None):
            captured_limits.append(max_items)
            yield [{"ID": "4", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

        async def iter_sync_activities(self, *, max_items: int | None):
            captured_limits.append(max_items)
            yield [{"ID": "5", "LAST_UPDATED": "2026-08-07T10:00:00+03:00"}]

        async def iter_sync_stage_history(
            self,
            *,
            entity_type_id: int,
            max_items: int | None,
        ):
            captured_limits.append(max_items)
            yield [
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
    assert captured_limits == [None] * 7

    status, counts = await get_bitrix_sync_status(settings)
    assert status.status == "completed"
    assert status.summary == result.counts
    assert counts == result.counts


@pytest.mark.asyncio
async def test_initial_sync_keeps_completed_pages_when_later_page_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingClient:
        async def iter_sync_deals(self, *, max_items: int | None):
            yield [{"ID": "1", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]
            yield [{"ID": "2", "DATE_MODIFY": "2026-08-07T10:01:00+03:00"}]

        async def iter_sync_leads(self, *, max_items: int | None):
            yield [{"ID": "3", "DATE_MODIFY": "2026-08-07T10:02:00+03:00"}]
            raise Bitrix24RequestError("Bitrix24 request timed out", error_code="TIMEOUT")
            yield []  # pragma: no cover

        async def iter_sync_contacts(self, *, max_items: int | None):
            yield []

        async def iter_sync_companies(self, *, max_items: int | None):
            yield []

        async def iter_sync_activities(self, *, max_items: int | None):
            yield []

        async def iter_sync_stage_history(
            self,
            *,
            entity_type_id: int,
            max_items: int | None,
        ):
            yield []

    monkeypatch.setattr(
        "app.services.bitrix24_sync.build_sync_client",
        lambda _settings: FailingClient(),
    )
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK_URL,
        database_path=str(tmp_path / "partial.db"),
    )

    with pytest.raises(Bitrix24RequestError, match="timed out"):
        await run_initial_bitrix_sync(settings)

    status, counts = await get_bitrix_sync_status(settings)
    assert status.status == "failed"
    assert status.summary == {"deal": 2, "lead": 1}
    assert counts == {"deal": 2, "lead": 1}
