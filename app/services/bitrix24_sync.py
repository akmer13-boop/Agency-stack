from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.integrations.bitrix24 import (
    Bitrix24ConfigurationError,
    Bitrix24RequestError,
)
from app.integrations.bitrix24.sync_client import SyncBitrix24Client
from app.proxy import build_proxy_url
from app.storage.crm_store import CrmStore, CrmSyncRunStatus


@dataclass(frozen=True, slots=True)
class BitrixSyncResult:
    run_id: int
    counts: dict[str, int]


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
) -> None:
    counts[entity_type] = 0
    await store.update_run_progress(run_id, counts)

    async for page in pages:
        written = await store.upsert_entities(
            entity_type,
            page,
            modified_field=modified_field,
        )
        counts[entity_type] += written
        await store.update_run_progress(run_id, counts)


async def run_initial_bitrix_sync(settings: Settings) -> BitrixSyncResult:
    if not settings.bitrix24_configured:
        raise Bitrix24ConfigurationError("BITRIX24_WEBHOOK_URL is not configured")

    store = CrmStore(settings.database_path)
    await store.initialize()
    run_id = await store.start_run()
    client = build_sync_client(settings)
    limit = settings.bitrix24_sync_item_limit
    counts: dict[str, int] = {}

    try:
        await _sync_pages(
            store,
            run_id,
            counts,
            "deal",
            client.iter_sync_deals(max_items=limit),
            "DATE_MODIFY",
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "lead",
            client.iter_sync_leads(max_items=limit),
            "DATE_MODIFY",
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "contact",
            client.iter_sync_contacts(max_items=limit),
            "DATE_MODIFY",
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "company",
            client.iter_sync_companies(max_items=limit),
            "DATE_MODIFY",
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "activity",
            client.iter_sync_activities(max_items=limit),
            "LAST_UPDATED",
        )
        await _sync_pages(
            store,
            run_id,
            counts,
            "deal_stage_history",
            client.iter_sync_stage_history(
                entity_type_id=2,
                max_items=limit,
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
            ),
            "CREATED_TIME",
        )
    except Bitrix24RequestError as exc:
        await store.fail_run(run_id, exc.error_code or "BITRIX24_REQUEST_ERROR")
        raise
    except Exception:
        await store.fail_run(run_id, "UNEXPECTED_SYNC_ERROR")
        raise

    await store.finish_run(run_id, counts)
    return BitrixSyncResult(run_id=run_id, counts=counts)


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
    }


def format_sync_result(result: BitrixSyncResult) -> str:
    labels = _labels()
    lines = [f"Bitrix24 full sync завершён. Run #{result.run_id}"]
    for entity_type, count in result.counts.items():
        lines.append(f"• {labels.get(entity_type, entity_type)}: {count}")
    lines.append(
        "Пагинация пройдена до реального конца данных по ID-cursor. "
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
