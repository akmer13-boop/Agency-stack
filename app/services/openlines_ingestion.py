from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any

import aiosqlite

from app.config import Settings
from app.integrations.bitrix24.client import Bitrix24RequestError
from app.integrations.bitrix24.openlines_client import OpenLinesReadOnlyClient
from app.proxy import build_proxy_url
from app.services.rop_directory import load_rop_directory
from app.storage.openlines_store import OpenLinesStore

_OWNER_TYPE_TO_ENTITY = {
    "1": "lead",
    "2": "deal",
    "3": "contact",
    "4": "company",
}

_DIALOG_PAGE_SIZE = 50
_DISCOVERY_BATCH_SIZE = 50


@dataclass(frozen=True, slots=True)
class CrmObjectCandidate:
    entity_type: str
    entity_id: str
    source_activity_max_id: int


@dataclass(frozen=True, slots=True)
class OpenLinesIngestionResult:
    crm_objects_discovered: int
    crm_objects_processed: int
    crm_objects_remaining: int
    discovery_batch_requests: int
    chats_discovered: int
    chats_processed: int
    backfill_complete_chats: int
    backfill_pending_chats: int
    dialog_pages_loaded: int
    messages_observed: int
    text_messages_observed: int
    manager_messages_observed: int
    client_messages_observed: int
    system_messages_observed: int
    bot_messages_observed: int
    unknown_messages_observed: int
    files_observed: int
    connectors: tuple[tuple[str, int], ...]
    errors: tuple[tuple[str, int], ...]


async def _discover_crm_objects(
    database_path: str,
    *,
    modified_since: str | None = None,
) -> list[CrmObjectCandidate]:
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT
                CAST(json_extract(payload_json, '$.OWNER_TYPE_ID') AS TEXT),
                CAST(json_extract(payload_json, '$.OWNER_ID') AS TEXT),
                MAX(CAST(entity_id AS INTEGER))
            FROM crm_active_entities
            WHERE entity_type = 'activity'
              AND UPPER(
                    COALESCE(
                        CAST(json_extract(payload_json, '$.PROVIDER_ID') AS TEXT),
                        ''
                    )
                  ) = 'IMOPENLINES_SESSION'
              AND json_extract(payload_json, '$.OWNER_ID') IS NOT NULL
              AND (
                    ? IS NULL
                    OR datetime(source_modified_at) >= datetime(?)
                  )
            GROUP BY 1, 2
            ORDER BY MAX(CAST(entity_id AS INTEGER)) DESC
            """,
            (modified_since, modified_since),
        )
        rows = await cursor.fetchall()

    result: list[CrmObjectCandidate] = []
    for owner_type, owner_id, activity_id in rows:
        entity_type = _OWNER_TYPE_TO_ENTITY.get(str(owner_type or ""))
        entity_id = str(owner_id or "")
        if not entity_type or not entity_id.isdigit():
            continue
        result.append(
            CrmObjectCandidate(
                entity_type=entity_type,
                entity_id=entity_id,
                source_activity_max_id=int(activity_id or 0),
            )
        )
    return result


def build_openlines_client(settings: Settings) -> OpenLinesReadOnlyClient:
    return OpenLinesReadOnlyClient(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_sync_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_sync_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
    )


def _dialog_messages(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("messages")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _numeric_message_ids(messages: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for item in messages:
        raw = item.get("id")
        try:
            message_id = int(raw)
        except (TypeError, ValueError):
            continue
        if message_id > 0:
            ids.append(message_id)
    return ids


async def _sync_chat(
    client: OpenLinesReadOnlyClient,
    store: OpenLinesStore,
    chat_id: str,
    *,
    directory_user_ids: frozenset[str],
    max_pages: int,
    request_delay_seconds: float,
) -> dict[str, int]:
    if max_pages < 1:
        raise ValueError("max_pages must be positive")

    result = Counter()

    latest_history = await client.get_session_history(chat_id)
    latest_written = await store.upsert_history(
        chat_id,
        latest_history,
        directory_user_ids=directory_user_ids,
    )
    expected = await store.expected_message_count_from_history(
        chat_id,
        latest_history,
    )

    result["messages_observed"] += latest_written.messages_observed
    result["text_messages"] += latest_written.text_messages
    result["manager_messages"] += latest_written.manager_messages
    result["client_messages"] += latest_written.client_messages
    result["system_messages"] += latest_written.system_messages
    result["bot_messages"] += latest_written.bot_messages
    result["unknown_messages"] += latest_written.unknown_messages
    result["files_observed"] += latest_written.files_observed

    state = await store.update_chat_sync_state(
        chat_id,
        expected_message_count=expected,
    )

    pages_used = 0

    if not state.backfill_complete:
        cursor = state.oldest_message_id

        while pages_used < max_pages:
            page = await client.get_dialog_messages(
                chat_id,
                last_id=cursor,
                limit=_DIALOG_PAGE_SIZE,
            )
            messages = _dialog_messages(page)
            ids = _numeric_message_ids(messages)

            if not messages:
                state = await store.update_chat_sync_state(
                    chat_id,
                    expected_message_count=expected,
                    backfill_complete=True,
                )
                break

            written = await store.upsert_dialog_page(
                chat_id,
                page,
                directory_user_ids=directory_user_ids,
                expected_message_count=expected,
            )
            pages_used += 1
            result["dialog_pages"] += 1
            result["messages_observed"] += written.messages_observed
            result["text_messages"] += written.text_messages
            result["manager_messages"] += written.manager_messages
            result["client_messages"] += written.client_messages
            result["system_messages"] += written.system_messages
            result["bot_messages"] += written.bot_messages
            result["unknown_messages"] += written.unknown_messages
            result["files_observed"] += written.files_observed

            if not ids:
                state = await store.update_chat_sync_state(
                    chat_id,
                    expected_message_count=expected,
                    pages_added=1,
                    error_code="DIALOG_PAGE_NO_NUMERIC_IDS",
                )
                break

            new_cursor = min(ids)
            if cursor is not None and new_cursor >= cursor:
                state = await store.update_chat_sync_state(
                    chat_id,
                    expected_message_count=expected,
                    pages_added=1,
                    error_code="DIALOG_CURSOR_NOT_ADVANCING",
                )
                break

            state = await store.update_chat_sync_state(
                chat_id,
                expected_message_count=expected,
                pages_added=1,
                backfill_complete=len(messages) < _DIALOG_PAGE_SIZE,
            )
            cursor = new_cursor

            if state.backfill_complete:
                break

            if request_delay_seconds:
                await asyncio.sleep(request_delay_seconds)

    if state.backfill_complete and pages_used < max_pages:
        state = await store.get_chat_sync_state(chat_id)
        newest = state.newest_message_id

        while newest is not None and pages_used < max_pages:
            page = await client.get_dialog_messages(
                chat_id,
                first_id=newest,
                limit=_DIALOG_PAGE_SIZE,
            )
            messages = _dialog_messages(page)
            ids = _numeric_message_ids(messages)

            if not messages:
                break

            written = await store.upsert_dialog_page(
                chat_id,
                page,
                directory_user_ids=directory_user_ids,
                expected_message_count=expected,
            )
            pages_used += 1
            result["dialog_pages"] += 1
            result["messages_observed"] += written.messages_observed
            result["text_messages"] += written.text_messages
            result["manager_messages"] += written.manager_messages
            result["client_messages"] += written.client_messages
            result["system_messages"] += written.system_messages
            result["bot_messages"] += written.bot_messages
            result["unknown_messages"] += written.unknown_messages
            result["files_observed"] += written.files_observed

            if not ids:
                await store.update_chat_sync_state(
                    chat_id,
                    expected_message_count=expected,
                    pages_added=1,
                    error_code="DIALOG_FORWARD_PAGE_NO_NUMERIC_IDS",
                )
                break

            new_newest = max(ids)
            if new_newest <= newest:
                await store.update_chat_sync_state(
                    chat_id,
                    expected_message_count=expected,
                    pages_added=1,
                    error_code="DIALOG_FORWARD_CURSOR_NOT_ADVANCING",
                )
                break

            newest = new_newest
            await store.update_chat_sync_state(
                chat_id,
                expected_message_count=expected,
                pages_added=1,
                backfill_complete=True,
            )

            if len(messages) < _DIALOG_PAGE_SIZE:
                break

            if request_delay_seconds:
                await asyncio.sleep(request_delay_seconds)

    return dict(result)


async def run_openlines_ingestion(
    settings: Settings,
    *,
    max_crm_objects: int = 200,
    max_chats: int = 50,
    max_pages_per_chat: int = 20,
    request_delay_seconds: float = 0.08,
    run_discovery: bool = True,
    run_backfill: bool = True,
    recent_modified_since: str | None = None,
) -> OpenLinesIngestionResult:
    if max_crm_objects < 1:
        raise ValueError("max_crm_objects must be positive")
    if max_chats < 1:
        raise ValueError("max_chats must be positive")
    if max_pages_per_chat < 1:
        raise ValueError("max_pages_per_chat must be positive")
    if not run_discovery and not run_backfill:
        raise ValueError("at least one Open Lines phase must be enabled")

    store = OpenLinesStore(settings.database_path)
    await store.initialize()
    client = build_openlines_client(settings)

    candidates: list[CrmObjectCandidate] = []
    pending: list[CrmObjectCandidate] = []
    connectors: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    chats_discovered = 0
    crm_objects_processed = 0
    discovery_batch_requests = 0
    processed_pending = 0

    if run_discovery:
        await store.seed_discovery_from_existing_links()

        candidates = await _discover_crm_objects(
            settings.database_path,
            modified_since=recent_modified_since,
        )

        for candidate in candidates:
            checkpoint = await store.discovery_checkpoint(
                candidate.entity_type,
                candidate.entity_id,
            )
            if candidate.source_activity_max_id > checkpoint:
                pending.append(candidate)

        discovery_batch = pending[:max_crm_objects]
        processed_pending = len(discovery_batch)

        for offset in range(0, len(discovery_batch), _DISCOVERY_BATCH_SIZE):
            group = discovery_batch[offset : offset + _DISCOVERY_BATCH_SIZE]
            requested = [(candidate.entity_type, candidate.entity_id) for candidate in group]

            try:
                batch_items = await client.get_crm_chats_batch(requested)
                discovery_batch_requests += 1
            except Bitrix24RequestError as exc:
                code = exc.error_code or "BITRIX24_BATCH_REQUEST_ERROR"
                errors[code] += len(group)
                for candidate in group:
                    await store.mark_discovery(
                        candidate.entity_type,
                        candidate.entity_id,
                        source_activity_max_id=candidate.source_activity_max_id,
                        chats_found=0,
                        error_code=code,
                    )
                if request_delay_seconds:
                    await asyncio.sleep(request_delay_seconds)
                continue

            by_object = {(item.entity_type, item.entity_id): item for item in batch_items}

            for candidate in group:
                item = by_object.get((candidate.entity_type, candidate.entity_id))
                if item is None:
                    code = "BATCH_ITEM_MISSING"
                    errors[code] += 1
                    await store.mark_discovery(
                        candidate.entity_type,
                        candidate.entity_id,
                        source_activity_max_id=candidate.source_activity_max_id,
                        chats_found=0,
                        error_code=code,
                    )
                    continue

                if item.error_code:
                    errors[item.error_code] += 1
                    await store.mark_discovery(
                        candidate.entity_type,
                        candidate.entity_id,
                        source_activity_max_id=candidate.source_activity_max_id,
                        chats_found=0,
                        error_code=item.error_code,
                    )
                    continue

                chats = list(item.chats)
                crm_objects_processed += 1
                chats_discovered += len(chats)

                for chat in chats:
                    await store.upsert_chat_link(
                        chat,
                        entity_type=candidate.entity_type,
                        entity_id=candidate.entity_id,
                    )
                    connector = str(
                        chat.get("CONNECTOR_TITLE") or chat.get("CONNECTOR_ID") or "UNKNOWN"
                    ).strip()
                    connectors[connector] += 1

                await store.mark_discovery(
                    candidate.entity_type,
                    candidate.entity_id,
                    source_activity_max_id=candidate.source_activity_max_id,
                    chats_found=len(chats),
                )

            if request_delay_seconds:
                await asyncio.sleep(request_delay_seconds)

    aggregate = Counter()
    chats_processed = 0

    if run_backfill:
        directory = await load_rop_directory(settings.database_path)
        directory_ids = frozenset(directory.users)
        if recent_modified_since:
            chat_ids = await store.list_chat_ids_for_recent_sync(
                modified_since=recent_modified_since,
                limit=max_chats,
            )
        else:
            chat_ids = await store.list_chat_ids_for_sync(limit=max_chats)

        for chat_id in chat_ids:
            try:
                chat_result = await _sync_chat(
                    client,
                    store,
                    chat_id,
                    directory_user_ids=directory_ids,
                    max_pages=max_pages_per_chat,
                    request_delay_seconds=request_delay_seconds,
                )
                aggregate.update(chat_result)
                chats_processed += 1
            except Bitrix24RequestError as exc:
                code = exc.error_code or "BITRIX24_REQUEST_ERROR"
                errors[code] += 1
                await store.update_chat_sync_state(
                    chat_id,
                    expected_message_count=None,
                    error_code=code,
                )

            if request_delay_seconds:
                await asyncio.sleep(request_delay_seconds)

    counts = await store.counts()

    return OpenLinesIngestionResult(
        crm_objects_discovered=len(candidates),
        crm_objects_processed=crm_objects_processed,
        crm_objects_remaining=max(0, len(pending) - processed_pending),
        discovery_batch_requests=discovery_batch_requests,
        chats_discovered=chats_discovered,
        chats_processed=chats_processed,
        backfill_complete_chats=counts.backfill_complete_chats,
        backfill_pending_chats=counts.backfill_pending_chats,
        dialog_pages_loaded=aggregate["dialog_pages"],
        messages_observed=aggregate["messages_observed"],
        text_messages_observed=aggregate["text_messages"],
        manager_messages_observed=aggregate["manager_messages"],
        client_messages_observed=aggregate["client_messages"],
        system_messages_observed=aggregate["system_messages"],
        bot_messages_observed=aggregate["bot_messages"],
        unknown_messages_observed=aggregate["unknown_messages"],
        files_observed=aggregate["files_observed"],
        connectors=tuple(connectors.most_common()),
        errors=tuple(errors.most_common()),
    )
