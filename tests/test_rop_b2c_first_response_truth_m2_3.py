from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.services import rop_b2c_first_response_truth as truth_module

CREATED_AT = "2026-08-18T07:00:00+00:00"
RESPONSE_AT = "2026-08-18T07:05:00+00:00"
OBSERVED_AT = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def prepare(database_path: str) -> None:
    connection = sqlite3.connect(database_path)

    connection.executescript(
        """
        CREATE TABLE crm_active_entities (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id)
        );

        CREATE TABLE openlines_crm_links (
            chat_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL
        );

        CREATE TABLE openlines_messages (
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            sent_at TEXT,
            sender_directory_user_id TEXT,
            sender_role TEXT NOT NULL
        );

        CREATE TABLE rop_voximplant_reconciliation_runs (
            id INTEGER PRIMARY KEY,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            pagination_complete INTEGER NOT NULL
        );

        CREATE TABLE rop_voximplant_statistic_facts (
            last_seen_run_id INTEGER NOT NULL,
            call_failed_code TEXT,
            call_start_at TEXT,
            crm_activity_id TEXT,
            crm_entity_type TEXT,
            crm_entity_id TEXT
        );
        """
    )

    connection.execute(
        """
        INSERT INTO rop_voximplant_reconciliation_runs (
            id,
            window_start,
            window_end,
            pagination_complete
        ) VALUES (1, ?, ?, 1)
        """,
        (
            "2026-08-01T00:00:00+00:00",
            "2026-08-18T12:00:00+00:00",
        ),
    )

    connection.commit()
    connection.close()


def put_entity(
    database_path: str,
    entity_type: str,
    entity_id: int,
    payload: dict[str, object],
) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        INSERT INTO crm_active_entities (
            entity_type,
            entity_id,
            payload_json
        ) VALUES (?, ?, ?)
        """,
        (
            entity_type,
            str(entity_id),
            json.dumps(payload),
        ),
    )
    connection.commit()
    connection.close()


def seed_lead(database_path: str, lead_id: int) -> None:
    put_entity(
        database_path,
        "lead",
        lead_id,
        {"DATE_CREATE": CREATED_AT},
    )


def seed_deal(
    database_path: str,
    deal_id: int,
    lead_id: int,
    *,
    contact_id: int | None = None,
) -> None:
    payload: dict[str, object] = {"LEAD_ID": str(lead_id)}

    if contact_id is not None:
        payload["CONTACT_ID"] = str(contact_id)

    put_entity(database_path, "deal", deal_id, payload)


def link(
    database_path: str,
    chat_id: str,
    entity_type: str,
    entity_id: int,
) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        INSERT INTO openlines_crm_links (
            chat_id,
            entity_type,
            entity_id
        ) VALUES (?, ?, ?)
        """,
        (chat_id, entity_type, str(entity_id)),
    )
    connection.commit()
    connection.close()


def message(database_path: str, chat_id: str) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        INSERT INTO openlines_messages (
            chat_id,
            message_id,
            sent_at,
            sender_directory_user_id,
            sender_role
        ) VALUES (?, ?, ?, ?, 'manager')
        """,
        (chat_id, "message-1", RESPONSE_AT, "manager-7"),
    )
    connection.commit()
    connection.close()


def allow_all_b2c(monkeypatch) -> None:
    monkeypatch.setattr(
        truth_module,
        "resolve_policy_scope",
        lambda *args, **kwargs: SimpleNamespace(
            eligible=True,
            profile_key="tourism_b2c",
        ),
    )


def build(database_path: str):
    return truth_module.build_b2c_first_response_truth(
        database_path,
        now=OBSERVED_AT,
    )


def test_m2_3_preserves_every_direct_lead_link(tmp_path: Path, monkeypatch) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)

    seed_lead(database_path, 101)
    seed_lead(database_path, 102)
    link(database_path, "chat-direct", "lead", 101)
    link(database_path, "chat-direct", "lead", 102)
    message(database_path, "chat-direct")

    result = build(database_path)

    assert result.ok == 2
    assert result.breach == 0
    assert result.blocked == 0


def test_m2_3_unique_secondary_link_can_add_safe_response(tmp_path: Path, monkeypatch) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)

    seed_lead(database_path, 101)
    seed_deal(database_path, 201, 101)
    link(database_path, "chat-deal", "deal", 201)
    message(database_path, "chat-deal")

    result = build(database_path)

    assert result.ok == 1
    assert result.breach == 0
    assert result.blocked == 0


def test_m2_3_conflicting_secondary_links_fail_closed(tmp_path: Path, monkeypatch) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)

    seed_lead(database_path, 101)
    seed_lead(database_path, 102)
    seed_deal(database_path, 201, 101, contact_id=501)
    seed_deal(database_path, 202, 102, contact_id=502)
    link(database_path, "chat-conflict", "deal", 201)
    link(database_path, "chat-conflict", "contact", 502)
    message(database_path, "chat-conflict")

    result = build(database_path)

    assert result.ok == 0
    assert result.breach == 0
    assert result.blocked == 2
    assert dict(result.blocked_reasons)["openlines_crm_link_ambiguous"] == 2
