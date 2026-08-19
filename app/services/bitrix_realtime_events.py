from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)

_EVENT_DEFINITIONS = {
    "oncrmleadadd": (
        "ONCRMLEADADD",
        "lead",
    ),
    "oncrmleadupdate": (
        "ONCRMLEADUPDATE",
        "lead",
    ),
    "oncrmleaddelete": (
        "ONCRMLEADDELETE",
        "lead",
    ),
    "oncrmdealadd": (
        "ONCRMDEALADD",
        "deal",
    ),
    "oncrmdealupdate": (
        "ONCRMDEALUPDATE",
        "deal",
    ),
    "oncrmdealdelete": (
        "ONCRMDEALDELETE",
        "deal",
    ),
    "oncrmdealmovetocategory": (
        "ONCRMDEALMOVETOCATEGORY",
        "deal",
    ),
    "oncrmactivityadd": (
        "ONCRMACTIVITYADD",
        "activity",
    ),
    "oncrmactivityupdate": (
        "ONCRMACTIVITYUPDATE",
        "activity",
    ),
    "oncrmactivitydelete": (
        "ONCRMACTIVITYDELETE",
        "activity",
    ),
    "onvoximplantcallinit": (
        "ONVOXIMPLANTCALLINIT",
        "call",
    ),
    "onvoximplantcallstart": (
        "ONVOXIMPLANTCALLSTART",
        "call",
    ),
    "onvoximplantcallend": (
        "ONVOXIMPLANTCALLEND",
        "call",
    ),
}


class BitrixRealtimeEventError(ValueError):
    def __init__(
        self,
        status_code: int,
        public_message: str,
    ) -> None:
        super().__init__(public_message)

        self.status_code = status_code
        self.public_message = public_message


@dataclass(frozen=True, slots=True)
class NormalizedBitrixEvent:
    event_key: str
    event_name: str
    event_ts: int
    event_handler_id: str
    entity_type: str
    entity_id: str
    call_id: str
    actor_user_id: str
    member_id: str
    domain: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BitrixEventIngestionResult:
    inbox_id: int
    inserted: bool
    event_name: str
    entity_type: str
    entity_id: str
    call_id: str


def _nested_keys(
    value: str,
) -> list[str]:
    return [
        item
        for item in re.findall(
            r"([^\[\]]+)",
            value,
        )
        if item
    ]


def _assign_nested(
    target: dict[str, Any],
    key: str,
    value: Any,
) -> None:
    parts = _nested_keys(key)

    if not parts:
        return

    current = target

    for part in parts[:-1]:
        child = current.get(part)

        if not isinstance(
            child,
            dict,
        ):
            child = {}
            current[part] = child

        current = child

    current[parts[-1]] = value


def _parse_form_body(
    body: bytes,
) -> dict[str, Any]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BitrixRealtimeEventError(
            400,
            "Invalid event body encoding",
        ) from exc

    parsed = parse_qs(
        text,
        keep_blank_values=True,
    )

    result: dict[str, Any] = {}

    for key, values in parsed.items():
        value: Any

        if len(values) == 1:
            value = values[0]
        else:
            value = values

        _assign_nested(
            result,
            key,
            value,
        )

    return result


def parse_bitrix_event_body(
    *,
    content_type: str,
    body: bytes,
    max_body_bytes: int = 262_144,
) -> dict[str, Any]:
    if not body:
        raise BitrixRealtimeEventError(
            400,
            "Empty event body",
        )

    if len(body) > max_body_bytes:
        raise BitrixRealtimeEventError(
            413,
            "Event body is too large",
        )

    media_type = (
        content_type.split(
            ";",
            1,
        )[0]
        .strip()
        .lower()
    )

    if media_type == "application/json":
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise BitrixRealtimeEventError(
                400,
                "Invalid JSON event body",
            ) from exc

        if not isinstance(
            value,
            dict,
        ):
            raise BitrixRealtimeEventError(
                400,
                "Event body must be an object",
            )

        return value

    if media_type in {
        "application/x-www-form-urlencoded",
        "",
    }:
        return _parse_form_body(body)

    raise BitrixRealtimeEventError(
        415,
        "Unsupported event content type",
    )


def _text(
    value: Any,
) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _mapping(
    value: Any,
) -> dict[str, Any]:
    return (
        value
        if isinstance(
            value,
            dict,
        )
        else {}
    )


def _domain_host(
    value: str,
) -> str:
    raw = value.strip()

    if not raw:
        return ""

    if "://" in raw:
        parsed = urlsplit(raw)

        return (parsed.hostname or "").lower()

    parsed = urlsplit("//" + raw)

    return (
        parsed.hostname
        or raw.split(
            "/",
            1,
        )[0]
    ).lower()


def _verify_application_token(
    *,
    expected: str,
    received: str,
) -> None:
    if not expected:
        raise BitrixRealtimeEventError(
            503,
            "Bitrix event token is not configured",
        )

    if not received or not secrets.compare_digest(
        received,
        expected,
    ):
        raise BitrixRealtimeEventError(
            401,
            "Invalid Bitrix event token",
        )


def _event_definition(
    raw_event: str,
) -> tuple[str, str]:
    key = raw_event.casefold()

    value = _EVENT_DEFINITIONS.get(key)

    if value is None:
        raise BitrixRealtimeEventError(
            422,
            "Unsupported Bitrix event",
        )

    return value


def _event_timestamp(
    value: Any,
) -> int:
    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise BitrixRealtimeEventError(
            422,
            "Bitrix event timestamp is missing or invalid",
        ) from exc

    if result <= 0:
        raise BitrixRealtimeEventError(
            422,
            "Bitrix event timestamp is missing or invalid",
        )

    return result


def _crm_entity_id(
    data: dict[str, Any],
) -> str:
    fields = _mapping(data.get("FIELDS"))

    return _text(fields.get("ID"))


def normalize_bitrix_event(
    payload: dict[str, Any],
    *,
    application_token: str,
    expected_member_id: str = "",
    expected_domain: str = "",
) -> NormalizedBitrixEvent:
    auth = _mapping(payload.get("auth"))

    _verify_application_token(
        expected=application_token,
        received=_text(auth.get("application_token")),
    )

    member_id = _text(auth.get("member_id"))

    domain = _domain_host(_text(auth.get("domain")))

    if expected_member_id and member_id != expected_member_id:
        raise BitrixRealtimeEventError(
            403,
            "Unexpected Bitrix member",
        )

    if expected_domain:
        expected_host = _domain_host(expected_domain)

        if domain != expected_host:
            raise BitrixRealtimeEventError(
                403,
                "Unexpected Bitrix domain",
            )

    raw_event = _text(payload.get("event"))

    (
        event_name,
        entity_type,
    ) = _event_definition(raw_event)

    event_ts = _event_timestamp(payload.get("ts"))

    event_handler_id = _text(payload.get("event_handler_id"))

    data = _mapping(payload.get("data"))

    entity_id = ""
    call_id = ""
    actor_user_id = ""

    if entity_type in {
        "lead",
        "deal",
        "activity",
    }:
        entity_id = _crm_entity_id(data)

        if not entity_id:
            raise BitrixRealtimeEventError(
                422,
                "CRM entity ID is missing",
            )

    elif entity_type == "call":
        call_id = _text(data.get("CALL_ID"))

        actor_user_id = _text(data.get("USER_ID"))

        if not call_id:
            raise BitrixRealtimeEventError(
                422,
                "Call ID is missing",
            )

    canonical = {
        "event": event_name,
        "event_handler_id": event_handler_id,
        "ts": event_ts,
        "member_id": member_id,
        "data": data,
    }

    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        default=str,
    )

    event_key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    return NormalizedBitrixEvent(
        event_key=event_key,
        event_name=event_name,
        event_ts=event_ts,
        event_handler_id=(event_handler_id),
        entity_type=entity_type,
        entity_id=entity_id,
        call_id=call_id,
        actor_user_id=(actor_user_id),
        member_id=member_id,
        domain=domain,
        data=data,
    )


async def ingest_bitrix_event(
    *,
    database_path: str,
    application_token: str,
    content_type: str,
    body: bytes,
    expected_member_id: str = "",
    expected_domain: str = "",
    max_body_bytes: int = 262_144,
) -> BitrixEventIngestionResult:
    payload = parse_bitrix_event_body(
        content_type=content_type,
        body=body,
        max_body_bytes=max_body_bytes,
    )

    event = normalize_bitrix_event(
        payload,
        application_token=application_token,
        expected_member_id=(expected_member_id),
        expected_domain=(expected_domain),
    )

    data_json = json.dumps(
        event.data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        default=str,
    )

    store = BitrixEventInboxStore(database_path)

    stored = await store.enqueue(
        event_key=event.event_key,
        event_name=event.event_name,
        event_ts=event.event_ts,
        event_handler_id=(event.event_handler_id),
        entity_type=(event.entity_type),
        entity_id=event.entity_id,
        call_id=event.call_id,
        actor_user_id=(event.actor_user_id),
        member_id=event.member_id,
        domain=event.domain,
        data_json=data_json,
    )

    return BitrixEventIngestionResult(
        inbox_id=stored.inbox_id,
        inserted=stored.inserted,
        event_name=event.event_name,
        entity_type=(event.entity_type),
        entity_id=event.entity_id,
        call_id=event.call_id,
    )
