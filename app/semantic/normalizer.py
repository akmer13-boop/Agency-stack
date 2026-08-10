from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.semantic.models import (
    SemanticActivity,
    SemanticDeal,
    SemanticLead,
    SemanticMappingError,
    SemanticStageEvent,
    SemanticUser,
)


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip() or None


def _required_id(payload: Mapping[str, Any], *, entity_type: str) -> str:
    value = _optional_text(payload.get("ID"))
    if value is None:
        raise SemanticMappingError(f"{entity_type}: required field ID is missing")
    return value


def _datetime(value: Any, *, field: str, entity_type: str) -> datetime | None:
    raw = _optional_text(value)
    if raw is None:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SemanticMappingError(f"{entity_type}: invalid datetime in {field}: {raw}") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _decimal(value: Any, *, field: str, entity_type: str) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SemanticMappingError(f"{entity_type}: invalid decimal in {field}: {value}") from exc


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "y", "yes"}


def normalize_lead(payload: Mapping[str, Any]) -> SemanticLead:
    entity_type = "lead"
    return SemanticLead(
        id=_required_id(payload, entity_type=entity_type),
        assigned_user_id=_optional_text(payload.get("ASSIGNED_BY_ID")),
        created_at=_datetime(
            payload.get("DATE_CREATE"),
            field="DATE_CREATE",
            entity_type=entity_type,
        ),
        updated_at=_datetime(
            payload.get("DATE_MODIFY"),
            field="DATE_MODIFY",
            entity_type=entity_type,
        ),
        status_id=_optional_text(payload.get("STATUS_ID")),
        status_semantic=_optional_text(payload.get("STATUS_SEMANTIC_ID")),
        source_id=_optional_text(payload.get("SOURCE_ID")),
        amount=_decimal(
            payload.get("OPPORTUNITY"),
            field="OPPORTUNITY",
            entity_type=entity_type,
        ),
        currency=_optional_text(payload.get("CURRENCY_ID")),
    )


def normalize_deal(payload: Mapping[str, Any]) -> SemanticDeal:
    entity_type = "deal"
    return SemanticDeal(
        id=_required_id(payload, entity_type=entity_type),
        assigned_user_id=_optional_text(payload.get("ASSIGNED_BY_ID")),
        created_at=_datetime(
            payload.get("DATE_CREATE"),
            field="DATE_CREATE",
            entity_type=entity_type,
        ),
        updated_at=_datetime(
            payload.get("DATE_MODIFY"),
            field="DATE_MODIFY",
            entity_type=entity_type,
        ),
        pipeline_id=_optional_text(payload.get("CATEGORY_ID")),
        stage_id=_optional_text(payload.get("STAGE_ID")),
        stage_semantic=_optional_text(payload.get("STAGE_SEMANTIC_ID")),
        moved_at=_datetime(
            payload.get("MOVED_TIME"),
            field="MOVED_TIME",
            entity_type=entity_type,
        ),
        close_date=_datetime(
            payload.get("CLOSEDATE"),
            field="CLOSEDATE",
            entity_type=entity_type,
        ),
        amount=_decimal(
            payload.get("OPPORTUNITY"),
            field="OPPORTUNITY",
            entity_type=entity_type,
        ),
        currency=_optional_text(payload.get("CURRENCY_ID")),
    )


def normalize_activity(payload: Mapping[str, Any]) -> SemanticActivity:
    entity_type = "activity"
    updated = payload.get("LAST_UPDATED") or payload.get("DATE_MODIFY")
    deadline = payload.get("DEADLINE") or payload.get("END_TIME")
    return SemanticActivity(
        id=_required_id(payload, entity_type=entity_type),
        owner_entity_type=_optional_text(payload.get("OWNER_TYPE_ID")),
        owner_entity_id=_optional_text(payload.get("OWNER_ID")),
        responsible_user_id=_optional_text(payload.get("RESPONSIBLE_ID")),
        activity_type=_optional_text(payload.get("TYPE_ID")),
        created_at=_datetime(
            payload.get("CREATED"),
            field="CREATED",
            entity_type=entity_type,
        ),
        updated_at=_datetime(
            updated,
            field="LAST_UPDATED",
            entity_type=entity_type,
        ),
        deadline_at=_datetime(
            deadline,
            field="DEADLINE",
            entity_type=entity_type,
        ),
        completed=_bool(payload.get("COMPLETED")),
        started_at=_datetime(
            payload.get("START_TIME"),
            field="START_TIME",
            entity_type=entity_type,
        ),
        ended_at=_datetime(
            payload.get("END_TIME"),
            field="END_TIME",
            entity_type=entity_type,
        ),
        provider_id=_optional_text(payload.get("PROVIDER_ID")),
        provider_type_id=_optional_text(payload.get("PROVIDER_TYPE_ID")),
        direction=_optional_text(payload.get("DIRECTION")),
        author_user_id=_optional_text(payload.get("AUTHOR_ID")),
        editor_user_id=_optional_text(payload.get("EDITOR_ID")),
        autocomplete_rule=_optional_text(payload.get("AUTOCOMPLETE_RULE")),
    )


def normalize_stage_event(
    payload: Mapping[str, Any],
    *,
    entity_type: str,
) -> SemanticStageEvent:
    if entity_type not in {"lead_stage_history", "deal_stage_history"}:
        raise SemanticMappingError(f"stage history: unsupported entity_type {entity_type}")

    if entity_type == "lead_stage_history":
        stage_id = payload.get("STATUS_ID")
        stage_semantic = payload.get("STATUS_SEMANTIC_ID")
    else:
        stage_id = payload.get("STAGE_ID")
        stage_semantic = payload.get("STAGE_SEMANTIC_ID")

    return SemanticStageEvent(
        id=_required_id(payload, entity_type=entity_type),
        entity_type=entity_type,
        owner_entity_id=_optional_text(payload.get("OWNER_ID")),
        stage_id=_optional_text(stage_id),
        stage_semantic=_optional_text(stage_semantic),
        occurred_at=_datetime(
            payload.get("CREATED_TIME"),
            field="CREATED_TIME",
            entity_type=entity_type,
        ),
    )


def normalize_user(payload: Mapping[str, Any]) -> SemanticUser:
    entity_type = "user"
    departments = payload.get("UF_DEPARTMENT")

    if departments in (None, ""):
        department_ids: tuple[str, ...] = ()
    elif isinstance(departments, (list, tuple)):
        department_ids = tuple(
            text for item in departments if (text := _optional_text(item)) is not None
        )
    else:
        value = _optional_text(departments)
        department_ids = (value,) if value is not None else ()

    return SemanticUser(
        id=_required_id(payload, entity_type=entity_type),
        first_name=_optional_text(payload.get("NAME")),
        last_name=_optional_text(payload.get("LAST_NAME")),
        middle_name=_optional_text(payload.get("SECOND_NAME")),
        active=_bool(payload.get("ACTIVE")),
        position=_optional_text(payload.get("WORK_POSITION")),
        department_ids=department_ids,
    )
