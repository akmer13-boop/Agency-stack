from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings
from app.integrations.bitrix24.client import (
    Bitrix24RequestError,
)
from app.services.bitrix24_sync import (
    build_sync_client,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
    BitrixInboxEvent,
)
from app.storage.crm_store import CrmStore

logger = logging.getLogger(__name__)


_REFRESH_EVENTS = frozenset(
    {
        "ONCRMLEADADD",
        "ONCRMLEADUPDATE",
        "ONCRMDEALADD",
        "ONCRMDEALUPDATE",
        "ONCRMDEALMOVETOCATEGORY",
        "ONCRMACTIVITYADD",
        "ONCRMACTIVITYUPDATE",
    }
)


_DELETE_EVENTS = frozenset(
    {
        "ONCRMLEADDELETE",
        "ONCRMDEALDELETE",
        "ONCRMACTIVITYDELETE",
    }
)


_CALL_EVENTS = frozenset(
    {
        "ONVOXIMPLANTCALLINIT",
        "ONVOXIMPLANTCALLSTART",
        "ONVOXIMPLANTCALLEND",
    }
)


_MODIFIED_FIELDS = {
    "lead": "DATE_MODIFY",
    "deal": "DATE_MODIFY",
    "activity": "LAST_UPDATED",
}


@dataclass(frozen=True, slots=True)
class EventEntitySnapshot:
    entity_type: str
    entity_id: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventProcessResult:
    inbox_id: int
    event_name: str
    outcome: str
    attempts: int
    entity_type: str = ""
    entity_id: str = ""
    call_id: str = ""
    result_code: str = ""
    error_code: str = ""


class EventEntityReader(Protocol):
    async def fetch(
        self,
        *,
        entity_type: str,
        entity_id: str,
    ) -> EventEntitySnapshot: ...


class BitrixRealtimeEntityReader:
    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self._client = build_sync_client(settings)

    async def fetch(
        self,
        *,
        entity_type: str,
        entity_id: str,
    ) -> EventEntitySnapshot:
        if entity_type not in {
            "lead",
            "deal",
            "activity",
        }:
            raise ValueError("unsupported_realtime_entity_type")

        if not entity_id or not entity_id.isdigit():
            raise ValueError("invalid_realtime_entity_id")

        numeric_id = int(entity_id)

        if entity_type == "lead":
            response = await self._client.call(
                "crm.lead.get",
                {
                    "id": numeric_id,
                },
            )

            raw = response.get("result")

            if not isinstance(
                raw,
                dict,
            ):
                raise Bitrix24RequestError(
                    "Bitrix24 returned an invalid lead snapshot",
                    error_code="INVALID_LEAD_SNAPSHOT",
                )

            payload = raw

        elif entity_type == "deal":
            response = await self._client.call(
                "crm.deal.get",
                {
                    "id": numeric_id,
                },
            )

            raw = response.get("result")

            if not isinstance(
                raw,
                dict,
            ):
                raise Bitrix24RequestError(
                    "Bitrix24 returned an invalid deal snapshot",
                    error_code="INVALID_DEAL_SNAPSHOT",
                )

            payload = raw

        else:
            response = await self._client.call(
                "crm.activity.list",
                {
                    "filter": {
                        "ID": numeric_id,
                    },
                    "select": [
                        "*",
                    ],
                },
            )

            raw = response.get("result")

            if (
                not isinstance(
                    raw,
                    list,
                )
                or not raw
                or not isinstance(
                    raw[0],
                    dict,
                )
            ):
                raise Bitrix24RequestError(
                    "Bitrix24 returned no activity snapshot",
                    error_code="ACTIVITY_NOT_FOUND",
                )

            payload = raw[0]

        returned_id = str(payload.get("ID") or "")

        if returned_id != entity_id:
            raise Bitrix24RequestError(
                "Bitrix24 entity ID mismatch",
                error_code="ENTITY_ID_MISMATCH",
            )

        return EventEntitySnapshot(
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )


def _safe_text(
    value: Any,
) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _safe_int(
    value: Any,
) -> int | None:
    if value in (
        None,
        "",
    ):
        return None

    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ):
        return None

    if result < 0:
        return None

    return result


def _load_event_data(
    event: BitrixInboxEvent,
) -> dict[str, Any]:
    try:
        value = json.loads(event.data_json)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_event_data_json") from exc

    if not isinstance(
        value,
        dict,
    ):
        raise ValueError("event_data_not_object")

    return value


def _call_fields(
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "call_failed_code": _safe_text(data.get("CALL_FAILED_CODE")),
        "call_duration_seconds": _safe_int(data.get("CALL_DURATION")),
        "crm_activity_id": _safe_text(data.get("CRM_ACTIVITY_ID")),
        "crm_entity_type": _safe_text(data.get("CRM_ENTITY_TYPE")).upper(),
        "crm_entity_id": _safe_text(data.get("CRM_ENTITY_ID")),
    }


async def _refresh_entity(
    event: BitrixInboxEvent,
    *,
    database_path: str,
    reader: EventEntityReader,
) -> str:
    snapshot = await reader.fetch(
        entity_type=event.entity_type,
        entity_id=event.entity_id,
    )

    store = CrmStore(database_path)

    await store.initialize()

    modified_field = _MODIFIED_FIELDS.get(snapshot.entity_type)

    written = await store.upsert_entities(
        snapshot.entity_type,
        [
            snapshot.payload,
        ],
        modified_field=modified_field,
    )

    if written != 1:
        raise RuntimeError("realtime_entity_upsert_failed")

    return "entity_refreshed"


async def process_next_bitrix_event(
    settings: Settings,
    *,
    reader: EventEntityReader | None = None,
    max_attempts: int = 3,
) -> EventProcessResult | None:
    inbox = BitrixEventInboxStore(settings.database_path)

    event = await inbox.claim_next(max_attempts=max_attempts)

    if event is None:
        return None

    try:
        if event.event_name in _REFRESH_EVENTS:
            active_reader = reader if reader is not None else BitrixRealtimeEntityReader(settings)

            result_code = await _refresh_entity(
                event,
                database_path=(settings.database_path),
                reader=active_reader,
            )

        elif event.event_name in _CALL_EVENTS:
            data = _load_event_data(event)

            fields = _call_fields(data)

            await inbox.record_call_evidence(
                event,
                **fields,
            )

            if event.event_name == "ONVOXIMPLANTCALLSTART":
                result_code = "call_conversation_start_recorded"

            elif event.event_name == "ONVOXIMPLANTCALLEND":
                result_code = "call_end_recorded"

            else:
                result_code = "call_init_recorded"

        elif event.event_name in _DELETE_EVENTS:
            await inbox.record_delete_observation(event)

            result_code = "delete_observation_recorded"

        else:
            raise ValueError("unsupported_processor_event")

        await inbox.complete(
            event.inbox_id,
            result_code=result_code,
        )

        return EventProcessResult(
            inbox_id=event.inbox_id,
            event_name=event.event_name,
            outcome="completed",
            attempts=event.attempts,
            entity_type=(event.entity_type),
            entity_id=(event.entity_id),
            call_id=(event.call_id),
            result_code=result_code,
        )

    except Bitrix24RequestError as exc:
        error_code = (exc.error_code or "BITRIX24_REQUEST_ERROR")[:120]

        await inbox.fail(
            event.inbox_id,
            error_code=error_code,
        )

        logger.warning(
            "Bitrix realtime event failed",
            extra={
                "event": "bitrix_realtime_event_failed",
                "inbox_id": event.inbox_id,
                "event_name": event.event_name,
                "error_code": error_code,
            },
        )

        return EventProcessResult(
            inbox_id=event.inbox_id,
            event_name=event.event_name,
            outcome="failed",
            attempts=event.attempts,
            entity_type=(event.entity_type),
            entity_id=(event.entity_id),
            call_id=(event.call_id),
            error_code=error_code,
        )

    except Exception as exc:
        error_code = (type(exc).__name__ or "EVENT_PROCESSOR_ERROR")[:120]

        await inbox.fail(
            event.inbox_id,
            error_code=error_code,
        )

        logger.exception(
            "Unexpected Bitrix realtime event processing failure",
            extra={
                "event": "bitrix_realtime_event_unexpected_failure",
                "inbox_id": event.inbox_id,
                "event_name": event.event_name,
                "error_code": error_code,
            },
        )

        return EventProcessResult(
            inbox_id=event.inbox_id,
            event_name=event.event_name,
            outcome="failed",
            attempts=event.attempts,
            entity_type=(event.entity_type),
            entity_id=(event.entity_id),
            call_id=(event.call_id),
            error_code=error_code,
        )


async def process_bitrix_event_batch(
    settings: Settings,
    *,
    limit: int = 20,
    max_attempts: int = 3,
    reader: EventEntityReader | None = None,
) -> list[EventProcessResult]:
    if limit < 1:
        raise ValueError("limit must be positive")

    results: list[EventProcessResult] = []

    for _ in range(limit):
        result = await process_next_bitrix_event(
            settings,
            reader=reader,
            max_attempts=max_attempts,
        )

        if result is None:
            break

        results.append(result)

    return results
