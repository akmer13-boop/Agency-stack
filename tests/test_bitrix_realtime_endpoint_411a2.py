from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app

TOKEN = "test-bitrix-event-token"
MEMBER = "member-123"
DOMAIN = "btx24.tur-mt.ru"


def settings(
    tmp_path: Path,
    **overrides,
) -> Settings:
    values = {
        "_env_file": None,
        "database_path": str(tmp_path / "events.db"),
        "bitrix_event_endpoint_enabled": True,
        "bitrix_event_application_token": TOKEN,
        "bitrix_event_member_id": MEMBER,
        "bitrix_event_domain": DOMAIN,
        "bitrix_event_max_body_bytes": 262_144,
    }

    values.update(overrides)

    return Settings(**values)


def payload() -> dict:
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
            "refresh_token": "also-must-not-be-persisted",
        },
    }


def client_for(
    config: Settings,
) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: config

    return TestClient(app)


def clear_override() -> None:
    app.dependency_overrides.clear()


def test_411a2_endpoint_disabled(
    tmp_path: Path,
) -> None:
    config = settings(
        tmp_path,
        bitrix_event_endpoint_enabled=False,
    )

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=payload(),
            )

        assert response.status_code == 503

    finally:
        clear_override()


def test_411a2_json_callback_returns_202(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=payload(),
            )

        assert response.status_code == 202

        body = response.json()

        assert body["status"] == "accepted"

        assert body["duplicate"] is False

        assert body["event"] == "ONCRMLEADUPDATE"

        assert body["entity_type"] == "lead"

        assert body["entity_id"] == "19531"

    finally:
        clear_override()


def test_411a2_agent_bearer_not_required(
    tmp_path: Path,
) -> None:
    config = settings(
        tmp_path,
        agent_api_token=("unrelated-agent-secret"),
    )

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=payload(),
            )

        assert response.status_code == 202

    finally:
        clear_override()


def test_411a2_wrong_token_returns_401(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    bad = payload()

    bad["auth"]["application_token"] = "wrong"

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=bad,
            )

        assert response.status_code == 401

    finally:
        clear_override()


def test_411a2_wrong_member_returns_403(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    bad = payload()

    bad["auth"]["member_id"] = "other"

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=bad,
            )

        assert response.status_code == 403

    finally:
        clear_override()


def test_411a2_wrong_domain_returns_403(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    bad = payload()

    bad["auth"]["domain"] = "wrong.example.com"

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=bad,
            )

        assert response.status_code == 403

    finally:
        clear_override()


def test_411a2_duplicate_callback(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    try:
        with client_for(config) as client:
            first = client.post(
                "/api/v1/bitrix/events",
                json=payload(),
            )

            second = client.post(
                "/api/v1/bitrix/events",
                json=payload(),
            )

        assert first.status_code == 202

        assert second.status_code == 202

        assert first.json()["duplicate"] is False

        assert second.json()["duplicate"] is True

        assert first.json()["event_id"] == second.json()["event_id"]

    finally:
        clear_override()


def test_411a2_form_callback(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    form = {
        "event": "ONCRMDEALUPDATE",
        "event_handler_id": "301",
        "data[FIELDS][ID]": "777",
        "ts": "1787120002",
        "auth[application_token]": TOKEN,
        "auth[member_id]": MEMBER,
        "auth[domain]": DOMAIN,
    }

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                content=urlencode(form),
                headers={"content-type": ("application/x-www-form-urlencoded")},
            )

        assert response.status_code == 202

        assert response.json()["entity_type"] == "deal"

        assert response.json()["entity_id"] == "777"

    finally:
        clear_override()


def test_411a2_inbox_written(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=payload(),
            )

        assert response.status_code == 202

    finally:
        clear_override()

    con = sqlite3.connect(config.database_path)

    row = con.execute(
        """
        SELECT
            event_name,
            entity_type,
            entity_id,
            status
        FROM bitrix_event_inbox
        LIMIT 1
        """
    ).fetchone()

    con.close()

    assert row == (
        "ONCRMLEADUPDATE",
        "lead",
        "19531",
        "pending",
    )


def test_411a2_secrets_not_persisted(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=payload(),
            )

        assert response.status_code == 202

    finally:
        clear_override()

    raw = Path(config.database_path).read_bytes()

    assert b"must-not-be-persisted" not in raw

    assert b"also-must-not-be-persisted" not in raw

    assert TOKEN.encode("utf-8") not in raw


def test_411a2_body_limit(
    tmp_path: Path,
) -> None:
    config = settings(
        tmp_path,
        bitrix_event_max_body_bytes=1024,
    )

    oversized = payload()

    oversized["data"]["PADDING"] = "x" * 5000

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=oversized,
            )

        assert response.status_code == 413

    finally:
        clear_override()


def test_411a2_call_start(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    call = {
        "event": "ONVOXIMPLANTCALLSTART",
        "event_handler_id": "401",
        "data": {
            "CALL_ID": "call-abc",
            "USER_ID": "77",
        },
        "ts": "1787120004",
        "auth": {
            "application_token": TOKEN,
            "member_id": MEMBER,
            "domain": DOMAIN,
        },
    }

    try:
        with client_for(config) as client:
            response = client.post(
                "/api/v1/bitrix/events",
                json=call,
            )

        assert response.status_code == 202

        body = response.json()

        assert body["event"] == "ONVOXIMPLANTCALLSTART"

        assert body["call_id"] == "call-abc"

        assert body["entity_type"] == "call"

    finally:
        clear_override()


def test_411a2_health_visibility(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    try:
        with client_for(config) as client:
            response = client.get("/health")

        assert response.status_code == 200

        assert response.json()["bitrix_realtime_events_enabled"] is True

    finally:
        clear_override()
