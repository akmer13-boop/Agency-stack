from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.integrations.bitrix24.event_privacy import (
    CALL_EVENT_DATA_FIELDS,
    minimize_bitrix_event_data,
)
from app.services.bitrix_realtime_events import (
    ingest_bitrix_event,
    normalize_bitrix_event,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)

TOKEN = "test-application-token"


def _call_payload(
    *,
    phone: str,
    comment: str,
) -> dict:
    return {
        "event": "ONVOXIMPLANTCALLEND",
        "event_handler_id": "901",
        "data": {
            "CALL_ID": "call-safe-1",
            "USER_ID": "55",
            "CALL_FAILED_CODE": "200",
            "CALL_DURATION": "48",
            "CALL_TYPE": "1",
            "CRM_ACTIVITY_ID": "900",
            "CRM_ENTITY_TYPE": "LEAD",
            "CRM_ENTITY_ID": "100",
            "PHONE_NUMBER": phone,
            "COMMENT": comment,
            "CALL_RECORD_URL": "https://private.example/record",
            "SUBJECT": "private subject",
            "EXTRA": {
                "PHONE": "+00000000999",
            },
        },
        "ts": "1787120005",
        "auth": {
            "application_token": TOKEN,
            "member_id": "member-1",
            "domain": "example.test",
            "access_token": "must-never-persist",
            "refresh_token": "must-never-persist-too",
        },
    }


def test_411a41_call_normalization_drops_pii_and_stabilizes_key() -> None:
    first = normalize_bitrix_event(
        _call_payload(
            phone="+00000000001",
            comment="private-A",
        ),
        application_token=TOKEN,
    )

    second = normalize_bitrix_event(
        _call_payload(
            phone="+00000000002",
            comment="private-B",
        ),
        application_token=TOKEN,
    )

    assert set(first.data) == set(CALL_EVENT_DATA_FIELDS)

    assert first.event_key == second.event_key

    serialized = json.dumps(
        first.data,
        ensure_ascii=False,
    )

    for forbidden in (
        "+00000000001",
        "private-A",
        "private.example",
        "private subject",
        "+00000000999",
    ):
        assert forbidden not in serialized


async def test_411a41_ingestion_never_persists_call_pii(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "events.db")

    result = await ingest_bitrix_event(
        database_path=database_path,
        application_token=TOKEN,
        content_type="application/json",
        body=json.dumps(
            _call_payload(
                phone="+00000000001",
                comment="private-A",
            )
        ).encode("utf-8"),
    )

    con = sqlite3.connect(database_path)

    row = con.execute(
        """
        SELECT data_json
        FROM bitrix_event_inbox
        WHERE id = ?
        """,
        (result.inbox_id,),
    ).fetchone()

    con.close()

    assert row is not None

    stored = json.loads(row[0])

    assert set(stored) == set(CALL_EVENT_DATA_FIELDS)

    raw = Path(database_path).read_bytes()

    for forbidden in (
        b"+00000000001",
        b"private-A",
        b"private.example",
        b"private subject",
        b"+00000000999",
        b"must-never-persist",
        b"must-never-persist-too",
    ):
        assert forbidden not in raw


async def test_411a41_store_enqueue_is_defense_in_depth(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "events.db")

    store = BitrixEventInboxStore(database_path)

    await store.enqueue(
        event_key="direct-call",
        event_name="ONVOXIMPLANTCALLEND",
        event_ts=1787120005,
        event_handler_id="901",
        entity_type="call",
        entity_id="",
        call_id="call-direct",
        actor_user_id="55",
        member_id="member",
        domain="example.test",
        data_json=json.dumps(
            {
                "CALL_ID": "call-direct",
                "USER_ID": "55",
                "CALL_FAILED_CODE": "200",
                "PHONE_NUMBER": "+00000012345",
                "COMMENT": "never-store-me",
            }
        ),
    )

    con = sqlite3.connect(database_path)

    row = con.execute(
        """
        SELECT data_json
        FROM bitrix_event_inbox
        WHERE event_key = 'direct-call'
        """
    ).fetchone()

    con.close()

    assert row is not None

    assert json.loads(row[0]) == {
        "CALL_FAILED_CODE": "200",
        "CALL_ID": "call-direct",
        "USER_ID": "55",
    }

    raw = Path(database_path).read_bytes()

    assert b"+00000012345" not in raw
    assert b"never-store-me" not in raw


def test_411a41_non_call_payload_remains_compatible() -> None:
    source = {
        "FIELDS": {
            "ID": "777",
        },
    }

    assert (
        minimize_bitrix_event_data(
            "deal",
            source,
        )
        == source
    )
