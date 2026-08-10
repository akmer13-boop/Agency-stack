from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite
import pytest

from app.semantic.models import SemanticMappingError
from app.semantic.normalizer import (
    normalize_activity,
    normalize_deal,
    normalize_lead,
    normalize_stage_event,
    normalize_user,
)
from app.semantic.repository import SemanticRepository


def test_normalize_lead() -> None:
    lead = normalize_lead(
        {
            "ID": "101",
            "ASSIGNED_BY_ID": "1891",
            "DATE_CREATE": "2026-08-10T10:00:00+03:00",
            "DATE_MODIFY": "2026-08-10T11:00:00+03:00",
            "STATUS_ID": "NEW",
            "STATUS_SEMANTIC_ID": "P",
            "SOURCE_ID": "WEB",
            "OPPORTUNITY": "1234.50",
            "CURRENCY_ID": "RUB",
        }
    )

    assert lead.id == "101"
    assert lead.assigned_user_id == "1891"
    assert lead.status_semantic == "P"
    assert lead.amount == Decimal("1234.50")
    assert lead.created_at == datetime(2026, 8, 10, 7, 0, tzinfo=UTC)


def test_normalize_deal() -> None:
    deal = normalize_deal(
        {
            "ID": "44",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:NEW",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "9",
            "DATE_CREATE": "2026-08-01T10:00:00Z",
            "MOVED_TIME": "2026-08-08T10:00:00Z",
            "OPPORTUNITY": "5000",
            "CURRENCY_ID": "RUB",
        }
    )

    assert deal.pipeline_id == "7"
    assert deal.stage_id == "C7:NEW"
    assert deal.amount == Decimal("5000")


def test_normalize_activity_is_deterministic() -> None:
    activity = normalize_activity(
        {
            "ID": "500",
            "OWNER_TYPE_ID": "1",
            "OWNER_ID": "101",
            "RESPONSIBLE_ID": "1891",
            "TYPE_ID": "2",
            "CREATED": "2026-08-10T10:00:00+03:00",
            "LAST_UPDATED": "2026-08-10T10:10:00+03:00",
            "COMPLETED": "Y",
        }
    )

    assert activity.owner_entity_id == "101"
    assert activity.activity_type == "2"
    assert activity.completed is True


def test_normalize_lead_stage_event_uses_status_fields() -> None:
    event = normalize_stage_event(
        {
            "ID": "900",
            "OWNER_ID": "101",
            "STATUS_ID": "CONVERTED",
            "STATUS_SEMANTIC_ID": "S",
            "CREATED_TIME": "2026-08-10T12:00:00Z",
        },
        entity_type="lead_stage_history",
    )

    assert event.stage_id == "CONVERTED"
    assert event.stage_semantic == "S"


def test_normalize_user_keeps_id_as_identity() -> None:
    user = normalize_user(
        {
            "ID": "1891",
            "NAME": "Call",
            "LAST_NAME": "Center",
            "ACTIVE": True,
            "WORK_POSITION": "Manager",
            "UF_DEPARTMENT": [10, "11"],
        }
    )

    assert user.id == "1891"
    assert user.department_ids == ("10", "11")


def test_missing_id_is_mapping_error() -> None:
    with pytest.raises(SemanticMappingError, match="required field ID"):
        normalize_lead({"DATE_CREATE": "2026-08-10T10:00:00Z"})


def test_invalid_datetime_is_mapping_error() -> None:
    with pytest.raises(SemanticMappingError, match="invalid datetime"):
        normalize_deal({"ID": "1", "DATE_CREATE": "not-a-date"})


@pytest.mark.asyncio
async def test_repository_projects_raw_lead(tmp_path) -> None:
    database_path = tmp_path / "crm.db"

    async with aiosqlite.connect(database_path) as database:
        await database.execute(
            """
            CREATE TABLE crm_raw_entities (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                source_modified_at TEXT,
                synced_at TEXT,
                PRIMARY KEY (entity_type, entity_id)
            )
            """
        )

        payload = {
            "ID": "101",
            "ASSIGNED_BY_ID": "1891",
            "DATE_CREATE": "2026-08-10T10:00:00Z",
            "STATUS_ID": "NEW",
            "STATUS_SEMANTIC_ID": "P",
        }

        await database.execute(
            """
            INSERT INTO crm_raw_entities (
                entity_type,
                entity_id,
                payload_json,
                payload_sha256
            )
            VALUES (?, ?, ?, ?)
            """,
            ("lead", "101", json.dumps(payload), "test"),
        )

        await database.commit()

    repository = SemanticRepository(str(database_path))
    leads = await repository.leads()

    assert len(leads) == 1
    assert leads[0].id == "101"
    assert leads[0].assigned_user_id == "1891"
