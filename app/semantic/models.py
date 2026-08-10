from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class SemanticMappingError(ValueError):
    """Raised when raw CRM evidence cannot be mapped safely."""


@dataclass(frozen=True, slots=True)
class SemanticLead:
    id: str
    assigned_user_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    status_id: str | None
    status_semantic: str | None
    source_id: str | None
    amount: Decimal
    currency: str | None


@dataclass(frozen=True, slots=True)
class SemanticDeal:
    id: str
    assigned_user_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    pipeline_id: str | None
    stage_id: str | None
    stage_semantic: str | None
    moved_at: datetime | None
    close_date: datetime | None
    amount: Decimal
    currency: str | None


@dataclass(frozen=True, slots=True)
class SemanticActivity:
    id: str
    owner_entity_type: str | None
    owner_entity_id: str | None
    responsible_user_id: str | None
    activity_type: str | None
    created_at: datetime | None
    updated_at: datetime | None
    deadline_at: datetime | None
    completed: bool
    started_at: datetime | None = None
    ended_at: datetime | None = None
    provider_id: str | None = None
    provider_type_id: str | None = None
    direction: str | None = None
    author_user_id: str | None = None
    editor_user_id: str | None = None
    autocomplete_rule: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticStageEvent:
    id: str
    entity_type: str
    owner_entity_id: str | None
    stage_id: str | None
    stage_semantic: str | None
    occurred_at: datetime | None


@dataclass(frozen=True, slots=True)
class SemanticUser:
    id: str
    first_name: str | None
    last_name: str | None
    middle_name: str | None
    active: bool
    position: str | None
    department_ids: tuple[str, ...]
