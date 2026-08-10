import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.integrations.bitrix24 import Bitrix24RequestError
from app.integrations.bitrix24.sync_client import SyncBitrix24Client
from app.services.bitrix24_sync import (
    Bitrix24SyncStateError,
    get_bitrix_sync_status,
    run_incremental_bitrix_sync,
    run_initial_bitrix_sync,
)
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
    assert await store.get_last_completed_run_started_at() is not None


@pytest.mark.asyncio
async def test_sync_client_reads_deal_stage_history_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/crm.stagehistory.list.json")
        payload = json.loads(request.content)
        assert payload["start"] == -1
        assert payload["entityTypeId"] == 2
        assert "STAGE_ID" in payload["select"]
        assert "STAGE_SEMANTIC_ID" in payload["select"]
        assert "STATUS_ID" not in payload["select"]
        return httpx.Response(
            200,
            json={
                "result": {
                    "items": [
                        {
                            "ID": "11",
                            "OWNER_ID": "77",
                            "STAGE_ID": "NEW",
                            "STAGE_SEMANTIC_ID": "P",
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
async def test_sync_client_reads_lead_status_history_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/crm.stagehistory.list.json")
        payload = json.loads(request.content)
        assert payload["start"] == -1
        assert payload["entityTypeId"] == 1
        assert "STATUS_ID" in payload["select"]
        assert "STATUS_SEMANTIC_ID" in payload["select"]
        assert "STAGE_ID" not in payload["select"]
        return httpx.Response(
            200,
            json={
                "result": {
                    "items": [
                        {
                            "ID": "12",
                            "OWNER_ID": "78",
                            "STATUS_ID": "CONVERTED",
                            "STATUS_SEMANTIC_ID": "S",
                            "CREATED_TIME": "2026-08-07T11:00:00+03:00",
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
    items = await client.list_sync_stage_history(entity_type_id=1, max_items=100)
    assert items[0]["STATUS_SEMANTIC_ID"] == "S"


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
async def test_sync_client_combines_incremental_filter_with_id_cursor() -> None:
    requests: list[dict[str, object]] = []
    checkpoint = "2026-08-07T10:00:00+00:00"

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        after_id = int(payload.get("filter", {}).get(">ID", 0))
        if after_id == 0:
            return httpx.Response(
                200,
                json={"result": [{"ID": str(item_id)} for item_id in range(1, 51)]},
            )
        return httpx.Response(200, json={"result": [{"ID": "51"}]})

    client = SyncBitrix24Client(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
        page_delay_seconds=0,
    )
    items = await client.list_sync_deals(modified_since=checkpoint)

    assert len(items) == 51
    assert requests[0]["filter"] == {">=DATE_MODIFY": checkpoint}
    assert requests[1]["filter"] == {">=DATE_MODIFY": checkpoint, ">ID": 50}


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


class FakeClient:
    def __init__(self) -> None:
        self.seen_since: list[str | None] = []

    async def iter_sync_deals(
        self, *, max_items: int | None, modified_since: str | None = None
    ):
        self.seen_since.append(modified_since)
        yield [{"ID": "1", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

    async def iter_sync_leads(
        self, *, max_items: int | None, modified_since: str | None = None
    ):
        self.seen_since.append(modified_since)
        yield [{"ID": "2", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

    async def iter_sync_contacts(
        self, *, max_items: int | None, modified_since: str | None = None
    ):
        self.seen_since.append(modified_since)
        yield [{"ID": "3", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

    async def iter_sync_companies(
        self, *, max_items: int | None, modified_since: str | None = None
    ):
        self.seen_since.append(modified_since)
        yield [{"ID": "4", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]

    async def iter_sync_activities(
        self, *, max_items: int | None, modified_since: str | None = None
    ):
        self.seen_since.append(modified_since)
        yield [{"ID": "5", "LAST_UPDATED": "2026-08-07T10:00:00+03:00"}]

    async def iter_sync_stage_history(
        self,
        *,
        entity_type_id: int,
        max_items: int | None,
        created_since: str | None = None,
    ):
        self.seen_since.append(created_since)
        if entity_type_id == 1:
            yield [
                {
                    "ID": "101",
                    "OWNER_ID": "2",
                    "CREATED_TIME": "2026-08-07T10:00:00+03:00",
                    "STATUS_ID": "NEW",
                    "STATUS_SEMANTIC_ID": "P",
                }
            ]
            return
        yield [
            {
                "ID": "102",
                "OWNER_ID": "1",
                "CREATED_TIME": "2026-08-07T10:00:00+03:00",
                "CATEGORY_ID": "0",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
            }
        ]

    async def list_departments(self):
        return [{"ID": "10", "NAME": "Продажи"}]

    async def list_users(self, *, max_items: int = 1000):
        assert max_items == 1000
        return [
            {
                "ID": "7",
                "NAME": "Тест",
                "LAST_NAME": "Менеджер",
                "ACTIVE": True,
                "UF_DEPARTMENT": [10],
            }
        ]


@pytest.mark.asyncio
async def test_initial_sync_persists_all_core_entity_types(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.bitrix24_sync.build_sync_client",
        lambda _settings: fake_client,
    )
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK_URL,
        database_path=str(tmp_path / "sync.db"),
    )

    result = await run_initial_bitrix_sync(settings)
    assert result.mode == "full"
    assert result.counts == {
        "deal": 1,
        "lead": 1,
        "contact": 1,
        "company": 1,
        "activity": 1,
        "deal_stage_history": 1,
        "lead_stage_history": 1,
        "department": 1,
        "user": 1,
    }
    assert fake_client.seen_since == [None] * 7

    status, counts = await get_bitrix_sync_status(settings)
    assert status.status == "completed"
    assert status.summary == result.counts
    assert counts == result.counts


@pytest.mark.asyncio
async def test_incremental_sync_uses_last_completed_run_with_overlap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.bitrix24_sync.build_sync_client",
        lambda _settings: fake_client,
    )
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK_URL,
        database_path=str(tmp_path / "incremental.db"),
        bitrix24_sync_overlap_minutes=5,
    )

    full_result = await run_initial_bitrix_sync(settings)
    assert full_result.mode == "full"
    fake_client.seen_since.clear()

    incremental_result = await run_incremental_bitrix_sync(settings)
    assert incremental_result.mode == "incremental"
    assert incremental_result.checkpoint is not None
    assert incremental_result.lead_history_repaired is False
    assert all(value == incremental_result.checkpoint for value in fake_client.seen_since)


@pytest.mark.asyncio
async def test_incremental_sync_repairs_legacy_lead_history_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(
        "app.services.bitrix24_sync.build_sync_client",
        lambda _settings: fake_client,
    )
    database_path = str(tmp_path / "repair.db")
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK_URL,
        database_path=database_path,
        bitrix24_sync_overlap_minutes=5,
    )

    await run_initial_bitrix_sync(settings)
    store = CrmStore(database_path)
    await store.upsert_entities(
        "lead_stage_history",
        [
            {
                "ID": "101",
                "OWNER_ID": "2",
                "CREATED_TIME": "2026-08-07T10:00:00+03:00",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
            }
        ],
        modified_field="CREATED_TIME",
    )
    fake_client.seen_since.clear()

    repaired = await run_incremental_bitrix_sync(settings)
    assert repaired.lead_history_repaired is True
    assert repaired.checkpoint is not None
    assert fake_client.seen_since[:-1] == [repaired.checkpoint] * 6
    assert fake_client.seen_since[-1] is None

    fake_client.seen_since.clear()
    normal = await run_incremental_bitrix_sync(settings)
    assert normal.lead_history_repaired is False
    assert normal.checkpoint is not None
    assert all(value == normal.checkpoint for value in fake_client.seen_since)


@pytest.mark.asyncio
async def test_incremental_sync_requires_completed_baseline(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK_URL,
        database_path=str(tmp_path / "empty.db"),
    )

    with pytest.raises(Bitrix24SyncStateError, match="completed full sync"):
        await run_incremental_bitrix_sync(settings)


@pytest.mark.asyncio
async def test_initial_sync_keeps_completed_pages_when_later_page_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingClient(FakeClient):
        async def iter_sync_deals(
            self, *, max_items: int | None, modified_since: str | None = None
        ):
            yield [{"ID": "1", "DATE_MODIFY": "2026-08-07T10:00:00+03:00"}]
            yield [{"ID": "2", "DATE_MODIFY": "2026-08-07T10:01:00+03:00"}]

        async def iter_sync_leads(
            self, *, max_items: int | None, modified_since: str | None = None
        ):
            yield [{"ID": "3", "DATE_MODIFY": "2026-08-07T10:02:00+03:00"}]
            raise Bitrix24RequestError("Bitrix24 request timed out", error_code="TIMEOUT")
            yield []  # pragma: no cover

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
