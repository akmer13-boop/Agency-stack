from __future__ import annotations

import json
from typing import Any

CALL_EVENT_DATA_FIELDS = frozenset(
    {
        "CALL_ID",
        "USER_ID",
        "CALL_FAILED_CODE",
        "CALL_DURATION",
        "CALL_TYPE",
        "CRM_ACTIVITY_ID",
        "CRM_ENTITY_TYPE",
        "CRM_ENTITY_ID",
    }
)

CALL_EVENT_MAX_TEXT_LENGTH = 512


def _safe_scalar(
    value: Any,
) -> str | int | float | bool | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value

    if isinstance(value, str):
        return value[:CALL_EVENT_MAX_TEXT_LENGTH]

    return None


def minimize_bitrix_event_data(
    entity_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    if entity_type != "call":
        return dict(data)

    result: dict[str, Any] = {}

    for key in sorted(CALL_EVENT_DATA_FIELDS):
        if key not in data:
            continue

        value = _safe_scalar(data[key])

        if value is not None:
            result[key] = value

    return result


def minimize_bitrix_event_data_json(
    entity_type: str,
    data_json: str,
) -> str:
    if entity_type != "call":
        return data_json

    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_call_event_data_json") from exc

    if not isinstance(data, dict):
        raise ValueError("call_event_data_not_object")

    minimized = minimize_bitrix_event_data(
        entity_type,
        data,
    )

    return json.dumps(
        minimized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
