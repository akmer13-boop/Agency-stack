from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.services.rop_b2c_stage_sla_truth import (
    build_b2c_stage_sla_truth,
)


def _put(
    db: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    payload: dict[str, object],
) -> None:
    db.execute(
        """
        INSERT INTO crm_active_entities (
            entity_type,
            entity_id,
            payload_json
        )
        VALUES (?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            json.dumps(payload),
        ),
    )


def _prepare(path: Path) -> None:
    db = sqlite3.connect(path)

    db.executescript(
        """
        CREATE TABLE crm_active_entities (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id)
        );

        CREATE TABLE crm_sync_runs (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE TABLE openlines_crm_links (
            chat_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL
        );

        CREATE TABLE openlines_messages (
            message_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            sender_role TEXT NOT NULL,
            sender_directory_user_id TEXT,
            sent_at TEXT
        );

        CREATE TABLE rop_voximplant_reconciliation_runs (
            id INTEGER PRIMARY KEY,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            pagination_complete INTEGER NOT NULL
        );

        CREATE TABLE rop_voximplant_statistic_facts (
            statistic_id TEXT PRIMARY KEY,
            call_start_at TEXT NOT NULL,
            call_failed_code TEXT NOT NULL,
            crm_activity_id TEXT,
            crm_entity_type TEXT,
            crm_entity_id TEXT,
            last_seen_run_id INTEGER NOT NULL
        );

        INSERT INTO crm_sync_runs (
            id,
            status,
            finished_at
        )
        VALUES (
            1,
            'completed',
            '2026-08-20 09:40:00'
        );

        INSERT INTO rop_voximplant_reconciliation_runs (
            id,
            window_start,
            window_end,
            pagination_complete
        )
        VALUES (
            1,
            '2026-06-20T00:00:00+00:00',
            '2026-08-20T09:35:00+00:00',
            1
        );
        """
    )

    _put(
        db,
        "user",
        "10",
        {
            "NAME": "Анна",
            "LAST_NAME": "Тестова",
            "UF_DEPARTMENT": [19],
        },
    )

    # 1: ATTENTION — no exact activity, full Vox coverage.
    _put(
        db,
        "deal",
        "1",
        {
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:NEW",
            "MOVED_TIME": "2026-08-20T09:00:00+00:00",
            "ASSIGNED_BY_ID": "10",
            "LEAD_ID": "101",
        },
    )

    # 2: OPEN — manager message resets timer.
    _put(
        db,
        "deal",
        "2",
        {
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:NEW",
            "MOVED_TIME": "2026-08-20T09:00:00+00:00",
            "ASSIGNED_BY_ID": "10",
            "LEAD_ID": "102",
        },
    )

    # 3: BLOCKED — successful mapped call can have reset timer.
    _put(
        db,
        "deal",
        "3",
        {
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:NEW",
            "MOVED_TIME": "2026-08-20T09:00:00+00:00",
            "ASSIGNED_BY_ID": "10",
            "LEAD_ID": "103",
        },
    )

    # 4: BLOCKED by policy.
    _put(
        db,
        "deal",
        "4",
        {
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:FINAL_INVOICE",
            "MOVED_TIME": "2026-08-20T09:00:00+00:00",
            "ASSIGNED_BY_ID": "10",
            "LEAD_ID": "104",
        },
    )

    # 5: BLOCKED because current stage is newer than common cutoff.
    _put(
        db,
        "deal",
        "5",
        {
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:NEW",
            "MOVED_TIME": "2026-08-20T09:36:00+00:00",
            "ASSIGNED_BY_ID": "10",
            "LEAD_ID": "105",
        },
    )

    for deal_id in ("1", "2", "3", "4"):
        _put(
            db,
            "deal_stage_history",
            f"h{deal_id}",
            {
                "OWNER_ID": deal_id,
                "STAGE_ID": (
                    "C7:FINAL_INVOICE"
                    if deal_id == "4"
                    else "C7:NEW"
                ),
                "CREATED_TIME": "2026-08-20T09:00:00+00:00",
            },
        )

    db.execute(
        """
        INSERT INTO openlines_crm_links (
            chat_id,
            entity_type,
            entity_id
        )
        VALUES ('200', 'deal', '2')
        """
    )

    db.execute(
        """
        INSERT INTO openlines_messages (
            message_id,
            chat_id,
            sender_role,
            sender_directory_user_id,
            sent_at
        )
        VALUES (
            'm1',
            '200',
            'manager',
            '10',
            '2026-08-20T09:25:00+00:00'
        )
        """
    )

    # Dummy row moves OpenLines watermark to 09:35 UTC.
    db.execute(
        """
        INSERT INTO openlines_messages (
            message_id,
            chat_id,
            sender_role,
            sender_directory_user_id,
            sent_at
        )
        VALUES (
            'm2',
            '999',
            'client',
            NULL,
            '2026-08-20T09:35:00+00:00'
        )
        """
    )

    db.execute(
        """
        INSERT INTO rop_voximplant_statistic_facts (
            statistic_id,
            call_start_at,
            call_failed_code,
            crm_activity_id,
            crm_entity_type,
            crm_entity_id,
            last_seen_run_id
        )
        VALUES (
            's1',
            '2026-08-20T09:20:00+00:00',
            '200',
            '',
            'LEAD',
            '103',
            1
        )
        """
    )

    db.commit()
    db.close()


def test_stage_sla_truth_is_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agency.db"
    _prepare(path)

    report = build_b2c_stage_sla_truth(
        str(path)
    )

    by_id = {
        item.deal_id: item
        for item in report.deals
    }

    assert report.cutoff_at.isoformat() == (
        "2026-08-20T09:35:00+00:00"
    )

    assert by_id[1].status == "ATTENTION"
    assert by_id[2].status == "OPEN"

    assert by_id[3].status == "BLOCKED"
    assert by_id[3].blocker_reason == (
        "successful_call_exact_reset_missing"
    )

    assert by_id[4].status == "BLOCKED"
    assert by_id[4].blocker_reason == (
        "return_to_client_date_not_configured"
    )

    assert by_id[5].status == "BLOCKED"
    assert by_id[5].blocker_reason == (
        "current_stage_not_valid_at_cutoff"
    )

    assert report.tracked_deals == 5
    assert report.open == 1
    assert report.attention == 1
    assert report.blocked == 3


def test_blocked_is_not_counted_as_attention(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agency.db"
    _prepare(path)

    report = build_b2c_stage_sla_truth(
        str(path)
    )

    assert report.attention == 1
    assert sum(
        count
        for _manager_id, _name, count
        in report.attention_by_manager
    ) == 1
