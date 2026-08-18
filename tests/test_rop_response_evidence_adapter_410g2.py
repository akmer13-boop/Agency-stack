from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.services.rop_policy_evaluation import (
    EvaluationVerdict,
    evaluate_first_response_case,
)
from app.services.rop_response_evidence_adapter import (
    TimingQuality,
    build_first_response_case_from_sources,
    merge_call_start_event,
    normalize_successful_call_statistic,
)


def dt(
    hour: int,
    minute: int,
) -> datetime:
    return datetime(
        2026,
        8,
        18,
        hour,
        minute,
        tzinfo=UTC,
    )


def create_db(
    path: Path,
) -> None:
    con = sqlite3.connect(path)

    con.executescript(
        """
        CREATE TABLE crm_raw_entities (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE openlines_crm_links (
            chat_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL
        );

        CREATE TABLE openlines_messages (
            message_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            sender_role TEXT NOT NULL,
            sender_directory_user_id TEXT,
            sent_at TEXT
        );
        """
    )

    lead = {
        "ID": "100",
        "DATE_CREATE": "2026-08-18T10:00:00+00:00",
    }

    con.execute(
        """
        INSERT INTO crm_raw_entities
        VALUES (?, ?, ?)
        """,
        (
            "lead",
            "100",
            json.dumps(lead),
        ),
    )

    con.commit()
    con.close()


def add_manager_message(
    path: Path,
    *,
    minute: int,
) -> None:
    con = sqlite3.connect(path)

    con.execute(
        """
        INSERT INTO openlines_crm_links
        VALUES ('77', 'lead', '100')
        """
    )

    con.execute(
        """
        INSERT INTO openlines_messages
        VALUES (?, '77', 'manager', '55', ?)
        """,
        (
            "501",
            dt(
                10,
                minute,
            ).isoformat(),
        ),
    )

    con.commit()
    con.close()


def add_lead_call_activity(
    path: Path,
    activity_id: str,
) -> None:
    con = sqlite3.connect(path)

    payload = {
        "ID": activity_id,
        "TYPE_ID": "2",
        "OWNER_TYPE_ID": "1",
        "OWNER_ID": "100",
    }

    con.execute(
        """
        INSERT INTO crm_raw_entities
        VALUES (?, ?, ?)
        """,
        (
            "activity",
            activity_id,
            json.dumps(payload),
        ),
    )

    con.commit()
    con.close()


def successful_stat(
    *,
    call_id: str = "call-1",
    activity_id: str = "900",
    minute: int = 5,
    duration: int = 0,
):
    return {
        "CALL_ID": call_id,
        "CALL_FAILED_CODE": "200",
        "CALL_TYPE": "1",
        "CALL_START_DATE": dt(
            10,
            minute,
        ).isoformat(),
        "CALL_DURATION": str(duration),
        "CRM_ACTIVITY_ID": activity_id,
        "CRM_ENTITY_TYPE": "LEAD",
        "CRM_ENTITY_ID": "100",
        "PORTAL_USER_ID": "55",
    }


def call_start_event(
    *,
    call_id: str = "call-1",
    minute: int = 7,
):
    return {
        "data": {
            "CALL_ID": call_id,
            "USER_ID": "55",
        },
        "ts": str(
            int(
                dt(
                    10,
                    minute,
                ).timestamp()
            )
        ),
    }


def test_410g2_code_200_is_success_even_with_zero_duration() -> None:
    fact = normalize_successful_call_statistic(successful_stat(duration=0))

    assert fact is not None

    assert fact.call_duration_seconds == 0

    assert fact.timing_quality is TimingQuality.CALL_START_ONLY


def test_410g2_failed_call_is_not_success() -> None:
    record = successful_stat()

    record["CALL_FAILED_CODE"] = "304"

    assert normalize_successful_call_statistic(record) is None


def test_410g2_call_start_event_produces_exact_response() -> None:
    fact = normalize_successful_call_statistic(successful_stat())

    assert fact is not None

    exact = merge_call_start_event(
        call_start_event(),
        fact,
    )

    assert exact is not None

    assert exact.response_at == dt(
        10,
        7,
    )

    assert exact.manager_user_id == 55


def test_410g2_message_can_be_exact_first_response(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"

    create_db(db)

    add_manager_message(
        db,
        minute=9,
    )

    result = build_first_response_case_from_sources(
        str(db),
        lead_id=100,
        openlines_source_complete=True,
        call_source_complete=True,
        as_of=dt(
            10,
            20,
        ),
    )

    assert result.case is not None
    assert result.blockers == ()

    assert result.exact_response_source == "openlines_message"

    evaluated = evaluate_first_response_case(result.case)

    assert evaluated.verdict is EvaluationVerdict.OK


def test_410g2_call_can_beat_later_message(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"

    create_db(db)

    add_manager_message(
        db,
        minute=12,
    )

    fact = normalize_successful_call_statistic(successful_stat())

    assert fact is not None

    exact = merge_call_start_event(
        call_start_event(minute=7),
        fact,
    )

    assert exact is not None

    result = build_first_response_case_from_sources(
        str(db),
        lead_id=100,
        exact_calls=(exact,),
        successful_call_facts=(fact,),
        openlines_source_complete=True,
        call_source_complete=True,
        as_of=dt(
            10,
            20,
        ),
    )

    assert result.case is not None

    assert result.exact_response_source == "voximplant_call_start_event"

    assert result.case.manager_response_at == dt(
        10,
        7,
    )


def test_410g2_activity_link_can_attach_call_to_lead(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"

    create_db(db)

    add_lead_call_activity(
        db,
        "900",
    )

    record = successful_stat()

    record["CRM_ENTITY_TYPE"] = "CONTACT"

    record["CRM_ENTITY_ID"] = "999"

    fact = normalize_successful_call_statistic(record)

    assert fact is not None

    exact = merge_call_start_event(
        call_start_event(),
        fact,
    )

    assert exact is not None

    result = build_first_response_case_from_sources(
        str(db),
        lead_id=100,
        exact_calls=(exact,),
        successful_call_facts=(fact,),
        openlines_source_complete=True,
        call_source_complete=True,
        as_of=dt(
            10,
            20,
        ),
    )

    assert result.case is not None

    assert result.exact_response_source == "voximplant_call_start_event"


def test_410g2_success_stat_alone_does_not_invent_exact_answer_time(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"

    create_db(db)

    fact = normalize_successful_call_statistic(successful_stat())

    assert fact is not None

    result = build_first_response_case_from_sources(
        str(db),
        lead_id=100,
        successful_call_facts=(fact,),
        openlines_source_complete=True,
        call_source_complete=True,
        as_of=dt(
            10,
            20,
        ),
    )

    assert result.case is None

    assert "successful_call_present_but_exact_answer_time_missing" in result.blockers


def test_410g2_incomplete_call_source_blocks_strict_sla(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"

    create_db(db)

    add_manager_message(
        db,
        minute=9,
    )

    result = build_first_response_case_from_sources(
        str(db),
        lead_id=100,
        openlines_source_complete=True,
        call_source_complete=False,
        as_of=dt(
            10,
            20,
        ),
    )

    assert result.case is None

    assert "call_source_not_complete" in result.blockers


def test_410g2_incomplete_openlines_source_blocks_strict_sla(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"

    create_db(db)

    result = build_first_response_case_from_sources(
        str(db),
        lead_id=100,
        openlines_source_complete=False,
        call_source_complete=True,
        as_of=dt(
            10,
            20,
        ),
    )

    assert result.case is None

    assert "openlines_source_not_complete" in result.blockers


def test_410g2_no_response_is_allowed_only_with_complete_sources(
    tmp_path: Path,
) -> None:
    db = tmp_path / "test.db"

    create_db(db)

    result = build_first_response_case_from_sources(
        str(db),
        lead_id=100,
        openlines_source_complete=True,
        call_source_complete=True,
        as_of=dt(
            10,
            16,
        ),
    )

    assert result.case is not None

    evaluated = evaluate_first_response_case(result.case)

    assert evaluated.verdict is EvaluationVerdict.BREACH
