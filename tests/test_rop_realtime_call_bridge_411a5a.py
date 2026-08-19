from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.services.bitrix_event_processor import (
    process_bitrix_event_batch,
)
from app.services.bitrix_realtime_events import (
    ingest_bitrix_event,
)
from app.services.rop_realtime_call_bridge import (
    build_exact_realtime_call_responses,
)

TOKEN = "test-application-token"


def settings(
    tmp_path: Path,
) -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "events.db"),
        bitrix24_webhook_url=("https://example.test/rest/1/abcdefgh/"),
    )


async def ingest_call_event(
    config: Settings,
    *,
    event_name: str,
    ts: int,
    call_id: str = "call-1",
    user_id: str = "55",
    call_type: str = "1",
    failed_code: str = "",
    crm_activity_id: str = "",
    crm_entity_type: str = "",
    crm_entity_id: str = "",
) -> None:
    data = {
        "CALL_ID": call_id,
        "USER_ID": user_id,
        "CALL_TYPE": call_type,
    }

    if failed_code:
        data["CALL_FAILED_CODE"] = failed_code

    if crm_activity_id:
        data["CRM_ACTIVITY_ID"] = crm_activity_id

    if crm_entity_type:
        data["CRM_ENTITY_TYPE"] = crm_entity_type

    if crm_entity_id:
        data["CRM_ENTITY_ID"] = crm_entity_id

    payload = {
        "event": event_name,
        "event_handler_id": "501",
        "data": data,
        "ts": str(ts),
        "auth": {
            "application_token": TOKEN,
            "member_id": "member",
            "domain": "example.test",
        },
    }

    await ingest_bitrix_event(
        database_path=(config.database_path),
        application_token=TOKEN,
        content_type=("application/json"),
        body=json.dumps(payload).encode("utf-8"),
    )


async def materialize(
    config: Settings,
) -> None:
    results = await process_bitrix_event_batch(
        config,
        limit=20,
    )

    assert all(result.outcome == "completed" for result in results)


async def test_411a5a_successful_call_builds_exact_response(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        ts=1787120000,
    )

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        ts=1787120060,
        failed_code="200",
        crm_activity_id="900",
        crm_entity_type="LEAD",
        crm_entity_id="100",
    )

    await materialize(config)

    result = build_exact_realtime_call_responses(config.database_path)

    assert result.blockers == ()

    assert result.successful_call_ids == ("call-1",)

    assert result.failed_call_ids == ()

    assert len(result.exact_calls) == 1

    exact = result.exact_calls[0]

    assert exact.call_id == "call-1"

    assert exact.crm_activity_id == "900"

    assert exact.crm_entity_type == "LEAD"

    assert exact.crm_entity_id == "100"

    assert exact.manager_user_id == 55

    assert exact.response_at == (
        datetime.fromtimestamp(
            1787120000,
            tz=UTC,
        )
    )

    assert exact.evidence.event_kind == "successful_phone_conversation_start"

    assert exact.evidence.actor_kind == "directory_user"


async def test_411a5a_failed_call_is_not_exact_response(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        ts=1787120000,
    )

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        ts=1787120060,
        failed_code="304",
        crm_activity_id="900",
        crm_entity_type="LEAD",
        crm_entity_id="100",
    )

    await materialize(config)

    result = build_exact_realtime_call_responses(config.database_path)

    assert result.exact_calls == ()

    assert result.successful_call_ids == ()

    assert result.failed_call_ids == ("call-1",)

    assert result.blockers == ()


async def test_411a5a_success_without_start_fails_closed(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        ts=1787120060,
        failed_code="200",
        crm_activity_id="900",
        crm_entity_type="LEAD",
        crm_entity_id="100",
    )

    await materialize(config)

    result = build_exact_realtime_call_responses(config.database_path)

    assert result.exact_calls == ()

    assert "successful_call_missing_exact_start:call-1" in result.blockers


async def test_411a5a_invalid_call_type_fails_closed(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        ts=1787120000,
        call_type="9",
    )

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        ts=1787120060,
        call_type="9",
        failed_code="200",
        crm_entity_type="LEAD",
        crm_entity_id="100",
    )

    await materialize(config)

    result = build_exact_realtime_call_responses(config.database_path)

    assert result.exact_calls == ()

    assert "successful_call_type_missing_or_invalid:call-1" in result.blockers


async def test_411a5a_manager_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        ts=1787120000,
        user_id="55",
    )

    await ingest_call_event(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        ts=1787120060,
        user_id="77",
        failed_code="200",
        crm_entity_type="LEAD",
        crm_entity_id="100",
    )

    await materialize(config)

    result = build_exact_realtime_call_responses(config.database_path)

    assert result.exact_calls == ()

    assert "successful_call_user_mismatch:call-1" in result.blockers


def test_411a5a_missing_tables_block(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "empty.db"

    sqlite3.connect(database_path).close()

    result = build_exact_realtime_call_responses(str(database_path))

    assert result.exact_calls == ()

    assert result.blockers == ("realtime_call_evidence_tables_missing",)


def test_411a5a_missing_database_blocks(
    tmp_path: Path,
) -> None:
    result = build_exact_realtime_call_responses(str(tmp_path / "missing.db"))

    assert result.exact_calls == ()

    assert result.blockers == ("realtime_call_database_missing",)
