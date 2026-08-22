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
            call_duration_seconds INTEGER,
            crm_activity_id TEXT,
            crm_entity_type TEXT,
            crm_entity_id TEXT,
            portal_user_id TEXT,
            call_type TEXT
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


def seed_lead(
    database_path: str,
    lead_id: int,
    *,
    created_at: str = CREATED_AT,
) -> None:
    put_entity(
        database_path,
        "lead",
        lead_id,
        {"DATE_CREATE": created_at},
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


def seed_user(database_path: str, user_id: int) -> None:
    put_entity(
        database_path,
        "user",
        user_id,
        {"NAME": "Менеджер", "LAST_NAME": str(user_id)},
    )


def seed_call(
    database_path: str,
    lead_id: int,
    *,
    call_start_at: str,
    duration: int = 60,
    portal_user_id: str = "55",
    call_type: str = "1",
) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        INSERT INTO rop_voximplant_statistic_facts (
            last_seen_run_id,
            call_failed_code,
            call_start_at,
            call_duration_seconds,
            crm_activity_id,
            crm_entity_type,
            crm_entity_id,
            portal_user_id,
            call_type
        )
        VALUES (1, '200', ?, ?, '', 'LEAD', ?, ?, ?)
        """,
        (
            call_start_at,
            duration,
            str(lead_id),
            portal_user_id,
            call_type,
        ),
    )
    connection.commit()
    connection.close()


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


def message(
    database_path: str,
    chat_id: str,
    *,
    sent_at: str = RESPONSE_AT,
) -> None:
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
        (chat_id, "message-1", sent_at, "manager-7"),
    )
    connection.commit()
    connection.close()


def allow_all_b2c(monkeypatch) -> None:
    monkeypatch.setattr(
        truth_module,
        "resolve_lead_policy_scopes",
        lambda _database_path, lead_ids: {
            lead_id: SimpleNamespace(
                eligible=True,
                profile_key="tourism_b2c",
            )
            for lead_id in lead_ids
        },
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


def test_calendar_window_excludes_older_leads_and_future_responses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)

    seed_lead(database_path, 100, created_at="2026-08-17T07:00:00+00:00")
    seed_lead(database_path, 101)
    link(database_path, "chat-direct", "lead", 101)
    message(
        database_path,
        "chat-direct",
        sent_at="2026-08-18T13:00:00+00:00",
    )

    result = truth_module.build_b2c_first_response_truth(
        database_path,
        now=OBSERVED_AT,
        window_start=datetime(2026, 8, 18, 0, 0, tzinfo=UTC),
    )

    assert result.all_leads_created == 1
    assert result.b2c_proven == 1
    assert result.ok == 0
    assert result.breach == 1
    assert result.unattributed_breaches == 1
    assert len(result.breach_leads) == 1
    assert result.breach_leads[0].lead_id == 101
    assert result.breach_leads[0].manager_id is None
    assert result.breach_leads[0].response_at is None


def test_truth_keeps_exact_attributed_breach_card_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)

    seed_lead(database_path, 101)
    link(database_path, "chat-direct", "lead", 101)
    message(
        database_path,
        "chat-direct",
        sent_at="2026-08-18T07:30:00+00:00",
    )

    result = build(database_path)

    assert result.breach == 1
    assert result.unattributed_breaches == 0
    assert result.breach_by_manager == (("manager-7", 1),)
    assert len(result.breach_leads) == 1

    evidence = result.breach_leads[0]
    assert evidence.lead_id == 101
    assert evidence.manager_id == "manager-7"
    assert evidence.response_at == datetime(
        2026,
        8,
        18,
        7,
        30,
        tzinfo=UTC,
    )
    assert evidence.threshold_business_seconds == 15 * 60
    assert evidence.elapsed_business_seconds == 30 * 60


def test_m4_6_exact_connected_call_counts_as_first_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)
    seed_lead(database_path, 101)
    seed_user(database_path, 55)
    seed_call(
        database_path,
        101,
        call_start_at="2026-08-18T07:05:00+00:00",
    )

    result = build(database_path)

    assert result.ok == 1
    assert result.breach == 0
    assert result.blocked == 0


def test_m4_6_late_exact_call_is_attributed_breach(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)
    seed_lead(database_path, 101)
    seed_user(database_path, 55)
    seed_call(
        database_path,
        101,
        call_start_at="2026-08-18T07:30:00+00:00",
    )

    result = build(database_path)

    assert result.ok == 0
    assert result.breach == 1
    assert result.blocked == 0
    assert result.breach_by_manager == (("55", 1),)
    assert result.breach_leads[0].manager_id == "55"
    assert result.breach_leads[0].response_at == datetime(
        2026,
        8,
        18,
        7,
        30,
        tzinfo=UTC,
    )


def test_m4_6_incomplete_successful_call_stays_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = str(tmp_path / "truth.db")
    prepare(database_path)
    allow_all_b2c(monkeypatch)
    seed_lead(database_path, 101)
    seed_call(
        database_path,
        101,
        call_start_at="2026-08-18T07:05:00+00:00",
        portal_user_id="",
    )

    result = build(database_path)

    assert result.ok == 0
    assert result.breach == 0
    assert result.blocked == 1
    assert dict(result.blocked_reasons) == {
        "successful_call_exact_answer_missing": 1,
    }
