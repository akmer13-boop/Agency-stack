from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from app.config import Settings
from app.integrations.bitrix24 import (
    Bitrix24ConfigurationError,
    Bitrix24RequestError,
)
from app.integrations.bitrix24.sync_client import SyncBitrix24Client
from app.proxy import build_proxy_url
from app.services.bitrix24_reconciliation import (
    RECONCILIABLE_ENTITY_TYPES,
    BitrixReconciliationAudit,
    build_and_store_bitrix_reconciliation_audit,
    format_bitrix_reconciliation_audit,
    get_last_bitrix_reconciliation_audit,
)
from app.storage.crm_store import CrmStore, CrmSyncRunStatus


class Bitrix24SyncStateError(RuntimeError):
    """Raised when incremental sync has no completed baseline to continue from."""


@dataclass(frozen=True, slots=True)
class BitrixSyncResult:
    run_id: int
    counts: dict[str, int]
    mode: str = "full"
    checkpoint: str | None = None
    lead_history_repaired: bool = False
    reconciliation_audit: BitrixReconciliationAudit | None = None


def build_sync_client(settings: Settings) -> SyncBitrix24Client:
    return SyncBitrix24Client(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_sync_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_sync_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
        retry_attempts=settings.bitrix24_sync_retry_attempts,
        retry_backoff_seconds=settings.bitrix24_sync_retry_backoff_seconds,
        page_delay_seconds=settings.bitrix24_sync_page_delay_seconds,
    )


async def _sync_pages(
    store: CrmStore,
    run_id: int,
    counts: dict[str, int],
    entity_type: str,
    pages: AsyncIterator[list[dict[str, Any]]],
    modified_field: str,
    *,
    observed_ids: set[str] | None = None,
) -> None:
    counts[entity_type] = 0
    await store.update_run_progress(run_id, counts)

    async for page in pages:
        if observed_ids is not None:
            observed_ids.update(str(item["ID"]) for item in page if item.get("ID") is not None)
        written = await store.upsert_entities(
            entity_type,
            page,
            modified_field=modified_field,
        )
        counts[entity_type] += written
        await store.update_run_progress(run_id, counts)


async def _sync_directory(
    store: CrmStore,
    run_id: int,
    counts: dict[str, int],
    client: SyncBitrix24Client,
) -> None:
    departments = await client.list_departments()
    counts["department"] = await store.upsert_entities("department", departments)
    await store.update_run_progress(run_id, counts)

    users = await client.list_users(max_items=1000)
    counts["user"] = await store.upsert_entities("user", users)
    await store.update_run_progress(run_id, counts)


async def _lead_history_needs_repair(database_path: str) -> bool:
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT 1
            FROM crm_raw_entities
            WHERE entity_type = 'lead_stage_history'
              AND (
                  json_extract(payload_json, '$.STATUS_ID') IS NULL
                  OR json_extract(payload_json, '$.STATUS_SEMANTIC_ID') IS NULL
              )
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
    return row is not None


def _checkpoint_with_overlap(started_at: str, overlap_minutes: int) -> str:
    try:
        checkpoint = datetime.fromisoformat(started_at)
    except ValueError as exc:
        raise Bitrix24SyncStateError("Last completed sync has an invalid timestamp") from exc
    if checkpoint.tzinfo is None:
        checkpoint = checkpoint.replace(tzinfo=UTC)
    checkpoint = checkpoint.astimezone(UTC) - timedelta(minutes=overlap_minutes)
    return checkpoint.isoformat(timespec="seconds")


async def _run_bitrix_sync(
    settings: Settings,
    *,
    mode: str,
    modified_since: str | None,
) -> BitrixSyncResult:
    if not settings.bitrix24_configured:
        raise Bitrix24ConfigurationError("BITRIX24_WEBHOOK_URL is not configured")

    store = CrmStore(settings.database_path)
    await store.initialize()
    run_id = await store.start_run()
    client = build_sync_client(settings)
    limit = settings.bitrix24_sync_item_limit
    counts: dict[str, int] = {}
    observed_ids_by_type: dict[str, set[str]] = (
        {entity_type: set() for entity_type in RECONCILIABLE_ENTITY_TYPES} if mode == "full" else {}
    )
    lead_history_repaired = mode == "incremental" and await _lead_history_needs_repair(
        settings.database_path
    )
    lead_history_since = None if lead_history_repaired else modified_since

    try:
        await _sync_pages(
            store,
            run_id,
            counts,
            "deal",
            client.iter_sync_deals(max_items=limit, modified_since=modified_since),
            "DATE_MODIFY",
            observed_ids=observed_ids_by_type.get("deal"),
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "lead",
            client.iter_sync_leads(max_items=limit, modified_since=modified_since),
            "DATE_MODIFY",
            observed_ids=observed_ids_by_type.get("lead"),
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "contact",
            client.iter_sync_contacts(max_items=limit, modified_since=modified_since),
            "DATE_MODIFY",
            observed_ids=observed_ids_by_type.get("contact"),
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "company",
            client.iter_sync_companies(max_items=limit, modified_since=modified_since),
            "DATE_MODIFY",
            observed_ids=observed_ids_by_type.get("company"),
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "activity",
            client.iter_sync_activities(max_items=limit, modified_since=modified_since),
            "LAST_UPDATED",
            observed_ids=observed_ids_by_type.get("activity"),
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "deal_stage_history",
            client.iter_sync_stage_history(
                entity_type_id=2,
                max_items=limit,
                created_since=modified_since,
            ),
            "CREATED_TIME",
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "lead_stage_history",
            client.iter_sync_stage_history(
                entity_type_id=1,
                max_items=limit,
                created_since=lead_history_since,
            ),
            "CREATED_TIME",
        )
        await _sync_directory(store, run_id, counts, client)
    except Bitrix24RequestError as exc:
        await store.fail_run(run_id, exc.error_code or "BITRIX24_REQUEST_ERROR")
        raise
    except Exception:
        await store.fail_run(run_id, "UNEXPECTED_SYNC_ERROR")
        raise

    await store.finish_run(run_id, counts)

    reconciliation_audit = None
    if mode == "full":
        reconciliation_audit = await build_and_store_bitrix_reconciliation_audit(
            settings,
            store,
            run_id=run_id,
            observed_ids_by_type=observed_ids_by_type,
            item_limit=limit,
        )

    return BitrixSyncResult(
        run_id=run_id,
        counts=counts,
        mode=mode,
        checkpoint=modified_since,
        lead_history_repaired=lead_history_repaired,
        reconciliation_audit=reconciliation_audit,
    )


async def run_initial_bitrix_sync(settings: Settings) -> BitrixSyncResult:
    return await _run_bitrix_sync(settings, mode="full", modified_since=None)


async def run_incremental_bitrix_sync(settings: Settings) -> BitrixSyncResult:
    if not settings.bitrix24_configured:
        raise Bitrix24ConfigurationError("BITRIX24_WEBHOOK_URL is not configured")

    store = CrmStore(settings.database_path)
    await store.initialize()
    last_completed_start = await store.get_last_completed_run_started_at()
    if not last_completed_start:
        raise Bitrix24SyncStateError("Incremental sync requires at least one completed full sync")

    checkpoint = _checkpoint_with_overlap(
        last_completed_start,
        settings.bitrix24_sync_overlap_minutes,
    )
    return await _run_bitrix_sync(
        settings,
        mode="incremental",
        modified_since=checkpoint,
    )


def get_bitrix_reconciliation_status(
    settings: Settings,
) -> BitrixReconciliationAudit | None:
    return get_last_bitrix_reconciliation_audit(settings)


async def get_bitrix_sync_status(settings: Settings) -> tuple[CrmSyncRunStatus, dict[str, int]]:
    store = CrmStore(settings.database_path)
    await store.initialize()
    return await store.get_last_run(), await store.count_by_type()


def _labels() -> dict[str, str]:
    return {
        "deal": "Сделки",
        "lead": "Лиды",
        "contact": "Контакты",
        "company": "Компании",
        "activity": "Активности",
        "deal_stage_history": "История стадий сделок",
        "lead_stage_history": "История стадий лидов",
        "department": "Подразделения",
        "user": "Сотрудники",
    }


def format_sync_result(result: BitrixSyncResult) -> str:
    labels = _labels()
    if result.mode == "incremental":
        lines = [f"Bitrix24 incremental sync завершён. Run #{result.run_id}"]
        if result.checkpoint:
            lines.append(f"Окно изменений с: {result.checkpoint}")
        for entity_type, count in result.counts.items():
            lines.append(f"• {labels.get(entity_type, entity_type)}: {count}")
        if result.lead_history_repaired:
            lines.append(
                "• Lead history repair: старые записи истории лидов без STATUS_* "
                "автоматически перечитаны полностью в read-only режиме."
            )
        lines.append(
            "Показано количество записей, полученных в окне изменений. "
            "Для одноразового lead history repair история лидов может быть перечитана "
            "полностью. Данные сохранены через upsert; запись в Bitrix24 не выполнялась."
        )
        return "\n".join(lines)

    lines = [f"Bitrix24 full sync завершён. Run #{result.run_id}"]
    for entity_type, count in result.counts.items():
        lines.append(f"• {labels.get(entity_type, entity_type)}: {count}")
    if result.reconciliation_audit is not None:
        lines.append("\n" + format_bitrix_reconciliation_audit(result.reconciliation_audit))
    lines.append(
        "Пагинация пройдена до реального конца данных по ID-cursor. "
        "Справочник сотрудников/подразделений обновлён в том же read-only запуске. "
        "Запись в Bitrix24 не выполнялась."
    )
    return "\n".join(lines)


def format_sync_status(status: CrmSyncRunStatus, counts: dict[str, int]) -> str:
    labels = _labels()
    if status.run_id is None:
        return "Bitrix24 sync ещё не запускался."

    lines = [
        "Bitrix24 sync status",
        f"Run: #{status.run_id}",
        f"Статус: {status.status}",
        f"Начало: {status.started_at or '—'}",
        f"Завершение: {status.finished_at or '—'}",
    ]
    if status.error_code:
        lines.append(f"Ошибка: {status.error_code}")

    if status.summary:
        lines.append("\nПрогресс текущего запуска:")
        for entity_type, count in status.summary.items():
            lines.append(f"• {labels.get(entity_type, entity_type)}: {count}")

    lines.append("\nЛокально сохранено всего:")
    for entity_type, count in counts.items():
        lines.append(f"• {labels.get(entity_type, entity_type)}: {count}")
    return "\n".join(lines)
