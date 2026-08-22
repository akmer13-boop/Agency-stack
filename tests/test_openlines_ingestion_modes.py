from pathlib import Path

import pytest

from app.config import Settings
from app.services import openlines_ingestion
from app.services.openlines_ingestion import run_openlines_ingestion
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_openlines_ingestion_requires_at_least_one_phase() -> None:
    settings = Settings(database_path=":memory:")

    with pytest.raises(
        ValueError,
        match="at least one Open Lines phase must be enabled",
    ):
        await run_openlines_ingestion(
            settings,
            run_discovery=False,
            run_backfill=False,
        )


@pytest.mark.asyncio
async def test_recent_discovery_scans_only_activities_in_window(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "10",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "100",
                "PROVIDER_ID": "IMOPENLINES_SESSION",
                "LAST_UPDATED": "2026-08-22T09:00:00+00:00",
            },
            {
                "ID": "20",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "200",
                "PROVIDER_ID": "IMOPENLINES_SESSION",
                "LAST_UPDATED": "2026-08-22T10:05:00+00:00",
            },
        ],
        modified_field="LAST_UPDATED",
    )

    candidates = await openlines_ingestion._discover_crm_objects(
        database_path,
        modified_since="2026-08-22T10:00:00+00:00",
    )

    assert [
        (item.entity_type, item.entity_id)
        for item in candidates
    ] == [("deal", "200")]


class _FakeCounts:
    backfill_complete_chats = 1
    backfill_pending_chats = 0


class _FakeDirectory:
    users = {}


class _FakeStore:
    def __init__(self, _database_path: str) -> None:
        pass

    async def initialize(self) -> None:
        pass

    async def seed_discovery_from_existing_links(self) -> None:
        raise AssertionError("discovery seed must not run in backfill-only mode")

    async def list_chat_ids_for_sync(self, *, limit: int) -> list[str]:
        assert limit == 5
        return []

    async def list_chat_ids_for_recent_sync(
        self,
        *,
        modified_since: str,
        limit: int,
    ) -> list[str]:
        assert modified_since == "2026-08-22T10:00:00+00:00"
        assert limit == 5
        return []

    async def counts(self) -> _FakeCounts:
        return _FakeCounts()


@pytest.mark.asyncio
async def test_backfill_only_skips_all_discovery_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_discovery(_database_path: str):
        raise AssertionError("CRM discovery scan must not run in backfill-only mode")

    async def fake_directory(_database_path: str):
        return _FakeDirectory()

    monkeypatch.setattr(openlines_ingestion, "OpenLinesStore", _FakeStore)
    monkeypatch.setattr(
        openlines_ingestion,
        "_discover_crm_objects",
        forbidden_discovery,
    )
    monkeypatch.setattr(
        openlines_ingestion,
        "load_rop_directory",
        fake_directory,
    )
    monkeypatch.setattr(
        openlines_ingestion,
        "build_openlines_client",
        lambda _settings: object(),
    )

    result = await openlines_ingestion.run_openlines_ingestion(
        Settings(database_path=":memory:"),
        max_crm_objects=1,
        max_chats=5,
        max_pages_per_chat=1,
        run_discovery=False,
        run_backfill=True,
    )

    assert result.crm_objects_discovered == 0
    assert result.crm_objects_processed == 0
    assert result.discovery_batch_requests == 0


@pytest.mark.asyncio
async def test_recent_mode_uses_bounded_recent_chat_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_directory(_database_path: str):
        return _FakeDirectory()

    monkeypatch.setattr(openlines_ingestion, "OpenLinesStore", _FakeStore)
    monkeypatch.setattr(
        openlines_ingestion,
        "load_rop_directory",
        fake_directory,
    )
    monkeypatch.setattr(
        openlines_ingestion,
        "build_openlines_client",
        lambda _settings: object(),
    )

    result = await openlines_ingestion.run_openlines_ingestion(
        Settings(database_path=":memory:"),
        max_crm_objects=1,
        max_chats=5,
        max_pages_per_chat=1,
        run_discovery=False,
        run_backfill=True,
        recent_modified_since="2026-08-22T10:00:00+00:00",
    )

    assert result.chats_processed == 0
