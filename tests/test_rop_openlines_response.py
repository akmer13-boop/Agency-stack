from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from app.services.rop_openlines_response import (
    build_openlines_response_report,
    format_openlines_response_for_ai,
)


async def _seed(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE crm_active_entities (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (entity_type, entity_id)
            );

            INSERT INTO crm_active_entities VALUES
                ('department', '5', '{"ID":"5","NAME":"Туризм"}'),
                (
                    'user',
                    '10',
                    '{"ID":"10","NAME":"Анна","LAST_NAME":"Иванова","ACTIVE":true,"UF_DEPARTMENT":["5"]}'
                ),
                (
                    'user',
                    '11',
                    '{"ID":"11","NAME":"Иван","LAST_NAME":"Петров","ACTIVE":false,"UF_DEPARTMENT":["5"]}'
                );

            CREATE TABLE conversation_threads (
                chat_id TEXT PRIMARY KEY,
                connector_title TEXT,
                last_sent_at TEXT,
                has_client INTEGER NOT NULL,
                has_dialogue INTEGER NOT NULL
            );

            INSERT INTO conversation_threads VALUES
                ('1', 'Telegram', '2026-08-16T10:10:00+00:00', 1, 1),
                ('2', 'WhatsApp', '2026-08-16T11:10:00+00:00', 1, 1),
                ('3', 'Telegram', '2026-08-16T11:20:00+00:00', 1, 0);

            CREATE TABLE conversation_response_intervals (
                chat_id TEXT NOT NULL,
                from_turn_index INTEGER NOT NULL,
                to_turn_index INTEGER NOT NULL,
                transition_type TEXT NOT NULL,
                manager_user_id TEXT,
                wait_seconds INTEGER NOT NULL,
                is_first_manager_response INTEGER NOT NULL,
                to_first_sent_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, from_turn_index, to_turn_index)
            );

            INSERT INTO conversation_response_intervals VALUES
                (
                    '1', 1, 2, 'client_to_manager', '10',
                    60, 1, '2026-08-16T10:01:00+00:00'
                ),
                (
                    '1', 3, 4, 'client_to_manager', '10',
                    300, 0, '2026-08-16T10:05:00+00:00'
                ),
                (
                    '2', 1, 2, 'client_to_manager', '11',
                    600, 1, '2026-08-16T11:05:00+00:00'
                ),
                (
                    '2', 2, 3, 'manager_to_client', '11',
                    120, 0, '2026-08-16T11:08:00+00:00'
                );

            CREATE TABLE conversation_thread_metrics (
                chat_id TEXT PRIMARY KEY,
                client_tail_after_dialogue INTEGER NOT NULL,
                initial_client_without_manager_response INTEGER NOT NULL
            );

            INSERT INTO conversation_thread_metrics VALUES
                ('1', 1, 0),
                ('2', 0, 0),
                ('3', 0, 1);

            CREATE TABLE conversation_manager_metrics (
                manager_user_id TEXT PRIMARY KEY
            );
            """
        )
        await db.commit()


@pytest.mark.asyncio
async def test_openlines_report_builds_factual_event_window(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    report = await build_openlines_response_report(
        database_path,
        7,
        now=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )

    assert report.response_events == 3
    assert report.first_response_events == 2
    assert report.response_median_seconds == 300
    assert report.response_p90_seconds == 600
    assert report.first_response_median_seconds == 330
    assert report.first_response_p90_seconds == 600
    assert report.current_client_tail_candidates == 1
    assert report.current_initial_no_response_candidates == 1
    assert len(report.managers) == 2
    assert report.managers[0].manager_user_id == "10"
    assert report.managers[0].active is True
    assert report.managers[1].manager_user_id == "11"
    assert report.managers[1].active is False


@pytest.mark.asyncio
async def test_openlines_manager_filter_does_not_claim_tail_ownership(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    report = await build_openlines_response_report(
        database_path,
        7,
        manager_id="10",
        now=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )

    assert report.response_events == 2
    assert report.first_response_events == 1
    assert report.current_client_tail_candidates is None
    assert report.current_initial_no_response_candidates is None

    text = format_openlines_response_for_ai(report)
    assert "Анна Иванова" in text
    assert "а не First Response SLA" in text
    assert "не приписываются ему" in text


@pytest.mark.asyncio
async def test_openlines_team_format_has_guardrails(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    report = await build_openlines_response_report(
        database_path,
        7,
        now=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )
    text = format_openlines_response_for_ai(report)

    assert "Open Lines Response Facts" in text
    assert "это НЕ рейтинг качества" in text
    assert "calendar elapsed factual evidence" in text
    assert "старый CRM lead response evidence остаётся отдельным источником" in text
    assert "inactive/history" in text
