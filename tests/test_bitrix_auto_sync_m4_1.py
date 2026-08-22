from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services import bitrix_auto_sync
from app.services.bitrix24_sync import (
    Bitrix24SyncStateError,
    BitrixSyncResult,
)


def _settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    openlines_enabled: bool = False,
    voximplant_enabled: bool = False,
) -> Settings:
    return Settings(
        _env_file=None,
        bitrix24_auto_sync_enabled=enabled,
        bitrix24_auto_sync_poll_seconds=300,
        bitrix24_auto_sync_health_path=str(
            tmp_path / "auto-sync-health.json"
        ),
        bitrix24_auto_sync_openlines_enabled=(
            openlines_enabled
        ),
        bitrix24_auto_sync_voximplant_enabled=(
            voximplant_enabled
        ),
        database_path=str(tmp_path / "agency.db"),
    )


@pytest.mark.asyncio
async def test_m4_1_auto_sync_is_disabled_by_default(
    tmp_path: Path,
) -> None:
    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        _settings(tmp_path, enabled=False)
    )

    assert result.outcome == "disabled"
    assert result.run_id is None


@pytest.mark.asyncio
async def test_m4_1_auto_sync_runs_existing_incremental_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Settings] = []

    async def fake_incremental(
        settings: Settings,
    ) -> BitrixSyncResult:
        calls.append(settings)
        return BitrixSyncResult(
            run_id=77,
            counts={
                "deal": 3,
                "lead": 2,
            },
            mode="incremental",
            checkpoint="2026-08-22T10:00:00+00:00",
        )

    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_incremental_bitrix_sync",
        fake_incremental,
    )

    settings = _settings(tmp_path)
    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        settings
    )

    assert calls == [settings]
    assert result.outcome == "completed"
    assert result.run_id == 77
    assert dict(result.counts) == {
        "deal": 3,
        "lead": 2,
    }


@pytest.mark.asyncio
async def test_m4_1_auto_sync_fails_closed_without_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_incremental(
        _settings: Settings,
    ) -> BitrixSyncResult:
        raise Bitrix24SyncStateError(
            "full sync required"
        )

    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_incremental_bitrix_sync",
        fake_incremental,
    )

    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        _settings(tmp_path)
    )

    assert result.outcome == "failed"
    assert result.error_code == "completed_full_sync_required"


@pytest.mark.asyncio
async def test_m4_1b_syncs_only_recent_openlines_after_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_incremental(
        _settings: Settings,
    ) -> BitrixSyncResult:
        return BitrixSyncResult(
            run_id=89,
            counts={"activity": 4, "lead": 2},
            mode="incremental",
            checkpoint="2026-08-22T10:00:00+00:00",
        )

    calls: list[dict[str, object]] = []

    async def fake_openlines(
        _settings: Settings,
        **kwargs: object,
    ) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            crm_objects_processed=2,
            chats_discovered=3,
            chats_processed=4,
            messages_observed=15,
            errors=(),
        )

    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_incremental_bitrix_sync",
        fake_incremental,
    )
    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_openlines_ingestion",
        fake_openlines,
    )

    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        _settings(
            tmp_path,
            openlines_enabled=True,
        )
    )

    assert result.outcome == "completed"
    assert result.openlines_outcome == "completed"
    assert result.openlines_chats_processed == 4
    assert result.openlines_messages_observed == 15
    assert calls == [
        {
            "max_crm_objects": 200,
            "max_chats": 50,
            "max_pages_per_chat": 3,
            "run_discovery": True,
            "run_backfill": True,
            "recent_modified_since": "2026-08-22T10:00:00+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_m4_1b_openlines_failure_does_not_erase_core_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_incremental(
        _settings: Settings,
    ) -> BitrixSyncResult:
        return BitrixSyncResult(
            run_id=90,
            counts={"deal": 1},
            mode="incremental",
            checkpoint="2026-08-22T10:00:00+00:00",
        )

    async def broken_openlines(
        _settings: Settings,
        **_kwargs: object,
    ) -> None:
        raise RuntimeError("local OpenLines failure")

    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_incremental_bitrix_sync",
        fake_incremental,
    )
    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_openlines_ingestion",
        broken_openlines,
    )

    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        _settings(
            tmp_path,
            openlines_enabled=True,
        )
    )

    assert result.outcome == "completed"
    assert result.run_id == 90
    assert result.openlines_outcome == "failed"
    assert result.openlines_error_code == "RuntimeError"


def test_m4_1_health_is_local_and_secret_free(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = bitrix_auto_sync.BitrixAutoSyncHealthStore(
        settings.bitrix24_auto_sync_health_path
    )
    store.record_startup(settings)
    store.record_attempt()
    store.record_success(
        BitrixSyncResult(
            run_id=88,
            counts={"deal": 4},
            mode="incremental",
            checkpoint="2026-08-22T10:05:00+00:00",
        )
    )

    text = bitrix_auto_sync.format_bitrix_auto_sync_status(
        settings
    )

    assert "включён: да" in text
    assert "последний Run ID: 88" in text
    assert "deal: 4" in text
    assert "BITRIX WRITES = NONE" in text
    assert "webhook" not in text.casefold()


def test_m4_1b_health_separates_core_and_openlines(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        openlines_enabled=True,
    )
    store = bitrix_auto_sync.BitrixAutoSyncHealthStore(
        settings.bitrix24_auto_sync_health_path
    )
    store.record_startup(settings)
    store.record_attempt()
    store.record_success(
        BitrixSyncResult(
            run_id=91,
            counts={"lead": 2},
            mode="incremental",
            checkpoint="2026-08-22T10:00:00+00:00",
        ),
        openlines_outcome="completed",
        openlines_crm_objects_processed=3,
        openlines_chats_discovered=2,
        openlines_chats_processed=4,
        openlines_messages_observed=12,
    )

    text = bitrix_auto_sync.format_bitrix_auto_sync_status(
        settings
    )

    assert "OpenLines · свежие чаты" in text
    assert "состояние: completed" in text
    assert "чатов обновлено: 4" in text
    assert "ошибок OpenLines: 0" in text


@pytest.mark.asyncio
async def test_m4_6_voximplant_bootstrap_runs_after_core_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_incremental(
        _settings: Settings,
    ) -> BitrixSyncResult:
        return BitrixSyncResult(
            run_id=92,
            counts={"lead": 1},
            mode="incremental",
            checkpoint="2026-08-23T09:55:00+00:00",
        )

    captured: dict[str, object] = {}

    async def fake_reconcile(
        database_path: str,
        client: object,
        *,
        window_start: datetime,
        window_end: datetime,
        max_pages: int,
    ) -> SimpleNamespace:
        captured.update(
            database_path=database_path,
            client=client,
            window_start=window_start,
            window_end=window_end,
            max_pages=max_pages,
        )
        return SimpleNamespace(
            run_id=12,
            fetched_rows=7,
            policy_candidate_calls=3,
        )

    fake_client = object()
    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_incremental_bitrix_sync",
        fake_incremental,
    )
    monkeypatch.setattr(
        bitrix_auto_sync,
        "build_sync_client",
        lambda _settings: fake_client,
    )
    monkeypatch.setattr(
        bitrix_auto_sync,
        "reconcile_voximplant_statistics",
        fake_reconcile,
    )

    settings = _settings(
        tmp_path,
        voximplant_enabled=True,
    )
    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        settings,
        now=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
    )

    assert result.outcome == "completed"
    assert result.voximplant_outcome == "completed"
    assert result.voximplant_run_id == 12
    assert result.voximplant_fetched_rows == 7
    assert result.voximplant_policy_candidate_calls == 3
    assert captured["database_path"] == settings.database_path
    assert captured["client"] is fake_client
    assert captured["window_start"] == datetime(
        2026,
        4,
        25,
        9,
        58,
        tzinfo=UTC,
    )
    assert captured["window_end"] == datetime(
        2026,
        8,
        23,
        9,
        58,
        tzinfo=UTC,
    )
    assert captured["max_pages"] == 500


@pytest.mark.asyncio
async def test_m4_6_voximplant_failure_keeps_core_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_incremental(
        _settings: Settings,
    ) -> BitrixSyncResult:
        return BitrixSyncResult(
            run_id=93,
            counts={"deal": 2},
            mode="incremental",
            checkpoint="2026-08-23T10:00:00+00:00",
        )

    async def broken_voximplant(
        _settings: Settings,
        *,
        now: datetime,
    ) -> None:
        raise RuntimeError("local Vox failure")

    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_incremental_bitrix_sync",
        fake_incremental,
    )
    monkeypatch.setattr(
        bitrix_auto_sync,
        "_run_voximplant_auto_sync",
        broken_voximplant,
    )

    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        _settings(tmp_path, voximplant_enabled=True)
    )

    assert result.outcome == "completed"
    assert result.run_id == 93
    assert result.voximplant_outcome == "failed"
    assert result.voximplant_error_code == "RuntimeError"


@pytest.mark.asyncio
async def test_m4_6_voximplant_api_error_is_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_incremental(
        _settings: Settings,
    ) -> BitrixSyncResult:
        return BitrixSyncResult(
            run_id=95,
            counts={"lead": 1},
            mode="incremental",
            checkpoint="2026-08-23T10:00:00+00:00",
        )

    async def denied_voximplant(
        _settings: Settings,
        *,
        now: datetime,
    ) -> None:
        raise bitrix_auto_sync.Bitrix24RequestError(
            "access denied",
            error_code="ACCESS_DENIED",
        )

    monkeypatch.setattr(
        bitrix_auto_sync,
        "run_incremental_bitrix_sync",
        fake_incremental,
    )
    monkeypatch.setattr(
        bitrix_auto_sync,
        "_run_voximplant_auto_sync",
        denied_voximplant,
    )

    result = await bitrix_auto_sync.run_bitrix_auto_sync_tick(
        _settings(tmp_path, voximplant_enabled=True)
    )

    assert result.outcome == "completed"
    assert result.run_id == 95
    assert result.voximplant_outcome == "failed"
    assert result.voximplant_error_code == "ACCESS_DENIED"


def test_m4_6_health_shows_voximplant_sidecar(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        voximplant_enabled=True,
    )
    store = bitrix_auto_sync.BitrixAutoSyncHealthStore(
        settings.bitrix24_auto_sync_health_path
    )
    store.record_startup(settings)
    store.record_success(
        BitrixSyncResult(
            run_id=94,
            counts={"activity": 2},
            mode="incremental",
            checkpoint="2026-08-23T10:00:00+00:00",
        ),
        voximplant_outcome="completed",
        voximplant_run_id=13,
        voximplant_window_start="2026-08-23T09:43:00+00:00",
        voximplant_window_end="2026-08-23T09:58:00+00:00",
        voximplant_fetched_rows=9,
        voximplant_policy_candidate_calls=4,
    )

    text = bitrix_auto_sync.format_bitrix_auto_sync_status(
        settings
    )

    assert "Voximplant · свежие звонки" in text
    assert "состояние: completed" in text
    assert "звонков получено: 9" in text
    assert "кандидатов для SLA: 4" in text
    assert "BITRIX WRITES = NONE" in text
