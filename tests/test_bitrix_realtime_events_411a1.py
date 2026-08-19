from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

import pytest

from app.services.bitrix_realtime_events import (
    BitrixRealtimeEventError,
    ingest_bitrix_event,
    normalize_bitrix_event,
    parse_bitrix_event_body,
)

TOKEN = "test-application-token"
MEMBER = "member-123"
DOMAIN = "btx24.tur-mt.ru"


def lead_event() -> dict:
    return {
        "event": "ONCRMLEADUPDATE",
        "event_handler_id": "201",
        "data": {
            "FIELDS": {
                "ID": "19531",
            },
        },
        "ts": "1787120000",
        "auth": {
            "application_token": TOKEN,
            "member_id": MEMBER,
            "domain": DOMAIN,
            "access_token": "must-not-be-persisted",
            "refresh_token": "must-not-be-persisted-either",
            "scope": "crm",
        },
    }


def call_event() -> dict:
    return {
        "event": "ONVOXIMPLANTCALLSTART",
        "event_handler_id": "301",
        "data": {
            "CALL_ID": "call-abc",
            "USER_ID": "77",
        },
        "ts": "1787120001",
        "auth": {
            "application_token": TOKEN,
            "member_id": MEMBER,
            "domain": DOMAIN,
            "access_token": "secret-access",
        },
    }


def test_411a1_json_body_parses() -> None:
    payload = lead_event()

    parsed = parse_bitrix_event_body(
        content_type="application/json",
        body=json.dumps(payload).encode("utf-8"),
    )

    assert parsed["data"]["FIELDS"]["ID"] == "19531"


def test_411a1_form_body_parses_nested_fields() -> None:
    body = urlencode(
        {
            "event": "ONCRMLEADUPDATE",
            "event_handler_id": "201",
            "data[FIELDS][ID]": "19531",
            "ts": "1787120000",
            "auth[application_token]": TOKEN,
            "auth[member_id]": MEMBER,
            "auth[domain]": DOMAIN,
        }
    ).encode("utf-8")

    parsed = parse_bitrix_event_body(
        content_type=("application/x-www-form-urlencoded"),
        body=body,
    )

    assert parsed["data"]["FIELDS"]["ID"] == "19531"

    assert parsed["auth"]["application_token"] == TOKEN


def test_411a1_wrong_token_rejected() -> None:
    payload = lead_event()

    payload["auth"]["application_token"] = "wrong"

    with pytest.raises(BitrixRealtimeEventError) as error:
        normalize_bitrix_event(
            payload,
            application_token=TOKEN,
        )

    assert error.value.status_code == 401


def test_411a1_expected_member_is_enforced() -> None:
    with pytest.raises(BitrixRealtimeEventError) as error:
        normalize_bitrix_event(
            lead_event(),
            application_token=TOKEN,
            expected_member_id=("other-member"),
        )

    assert error.value.status_code == 403


def test_411a1_expected_domain_is_enforced() -> None:
    with pytest.raises(BitrixRealtimeEventError) as error:
        normalize_bitrix_event(
            lead_event(),
            application_token=TOKEN,
            expected_domain=("wrong.example.com"),
        )

    assert error.value.status_code == 403


def test_411a1_domain_normalization_accepts_https() -> None:
    event = normalize_bitrix_event(
        lead_event(),
        application_token=TOKEN,
        expected_domain=("https://btx24.tur-mt.ru/"),
    )

    assert event.domain == DOMAIN


def test_411a1_unknown_event_rejected() -> None:
    payload = lead_event()

    payload["event"] = "ONUNSUPPORTEDTHING"

    with pytest.raises(BitrixRealtimeEventError) as error:
        normalize_bitrix_event(
            payload,
            application_token=TOKEN,
        )

    assert error.value.status_code == 422


def test_411a1_call_start_extracts_call_and_user() -> None:
    event = normalize_bitrix_event(
        call_event(),
        application_token=TOKEN,
        expected_member_id=MEMBER,
        expected_domain=DOMAIN,
    )

    assert event.event_name == "ONVOXIMPLANTCALLSTART"

    assert event.entity_type == "call"

    assert event.call_id == "call-abc"

    assert event.actor_user_id == "77"


async def test_411a1_event_is_persisted_without_auth_tokens(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"

    body = json.dumps(lead_event()).encode("utf-8")

    result = await ingest_bitrix_event(
        database_path=str(db),
        application_token=TOKEN,
        content_type="application/json",
        body=body,
        expected_member_id=MEMBER,
        expected_domain=DOMAIN,
    )

    assert result.inserted is True
    assert result.entity_type == "lead"
    assert result.entity_id == "19531"

    con = sqlite3.connect(db)

    row = con.execute(
        """
        SELECT
            event_name,
            entity_type,
            entity_id,
            member_id,
            domain,
            data_json
        FROM bitrix_event_inbox
        WHERE id = ?
        """,
        (result.inbox_id,),
    ).fetchone()

    con.close()

    assert row is not None

    serialized = json.dumps(
        row,
        ensure_ascii=False,
    )

    assert "must-not-be-persisted" not in serialized

    assert "must-not-be-persisted-either" not in serialized

    assert TOKEN not in serialized

    assert row[0] == "ONCRMLEADUPDATE"

    assert row[1] == "lead"
    assert row[2] == "19531"


async def test_411a1_duplicate_callback_is_idempotent(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"

    body = json.dumps(lead_event()).encode("utf-8")

    first = await ingest_bitrix_event(
        database_path=str(db),
        application_token=TOKEN,
        content_type="application/json",
        body=body,
    )

    second = await ingest_bitrix_event(
        database_path=str(db),
        application_token=TOKEN,
        content_type="application/json",
        body=body,
    )

    assert first.inserted is True
    assert second.inserted is False

    assert first.inbox_id == second.inbox_id

    con = sqlite3.connect(db)

    count = con.execute(
        """
        SELECT COUNT(*)
        FROM bitrix_event_inbox
        """
    ).fetchone()[0]

    con.close()

    assert count == 1


async def test_411a1_call_event_persisted(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"

    result = await ingest_bitrix_event(
        database_path=str(db),
        application_token=TOKEN,
        content_type="application/json",
        body=json.dumps(call_event()).encode("utf-8"),
    )

    assert result.inserted is True
    assert result.entity_type == "call"
    assert result.call_id == "call-abc"

    con = sqlite3.connect(db)

    row = con.execute(
        """
        SELECT
            call_id,
            actor_user_id
        FROM bitrix_event_inbox
        WHERE id = ?
        """,
        (result.inbox_id,),
    ).fetchone()

    con.close()

    assert row == (
        "call-abc",
        "77",
    )


def test_411a1_body_size_guard() -> None:
    with pytest.raises(BitrixRealtimeEventError) as error:
        parse_bitrix_event_body(
            content_type=("application/json"),
            body=b"x" * 2000,
            max_body_bytes=1000,
        )

    assert error.value.status_code == 413
