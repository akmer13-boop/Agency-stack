from pathlib import Path

import aiosqlite
import pytest

from app.services.conversation_aggregates import build_conversation_aggregates


async def _seed(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """
            CREATE TABLE crm_active_entities (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (entity_type, entity_id)
            );

            INSERT INTO crm_active_entities VALUES
                ('user', '10', '{"ID":"10","ACTIVE":true}'),
                ('user', '11', '{"ID":"11","ACTIVE":false}');

            CREATE TABLE conversation_threads (
                chat_id TEXT PRIMARY KEY,
                connector_id TEXT,
                connector_title TEXT,
                first_message_id INTEGER NOT NULL,
                last_message_id INTEGER NOT NULL,
                first_sent_at TEXT,
                last_sent_at TEXT,
                human_message_count INTEGER NOT NULL,
                human_turn_count INTEGER NOT NULL,
                client_message_count INTEGER NOT NULL,
                manager_message_count INTEGER NOT NULL,
                distinct_manager_count INTEGER NOT NULL,
                first_role TEXT NOT NULL,
                last_role TEXT NOT NULL,
                has_client INTEGER NOT NULL,
                has_manager INTEGER NOT NULL,
                has_dialogue INTEGER NOT NULL,
                client_tail_after_dialogue INTEGER NOT NULL,
                crm_link_count INTEGER NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO conversation_threads VALUES
                ('1', NULL, 'Telegram', 1, 4,
                 '2026-08-16T10:00:00+00:00',
                 '2026-08-16T10:10:00+00:00',
                 4, 4, 2, 2, 2, 'client', 'manager',
                 1, 1, 1, 0, 2, CURRENT_TIMESTAMP),
                ('2', NULL, 'WhatsApp', 5, 6,
                 '2026-08-16T11:00:00+00:00',
                 '2026-08-16T11:05:00+00:00',
                 2, 2, 1, 1, 1, 'client', 'manager',
                 1, 1, 1, 0, 1, CURRENT_TIMESTAMP);

            CREATE TABLE conversation_thread_metrics (
                chat_id TEXT PRIMARY KEY,
                conversation_duration_seconds INTEGER NOT NULL,
                first_client_turn_index INTEGER,
                first_manager_response_turn_index INTEGER,
                first_manager_response_user_id TEXT,
                first_response_seconds INTEGER,
                first_response_available INTEGER NOT NULL,
                initial_client_without_manager_response INTEGER NOT NULL,
                client_to_manager_interval_count INTEGER NOT NULL,
                manager_to_client_interval_count INTEGER NOT NULL,
                manager_handoff_count INTEGER NOT NULL,
                last_human_role TEXT NOT NULL,
                client_tail_after_dialogue INTEGER NOT NULL,
                distinct_manager_count INTEGER NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO conversation_thread_metrics VALUES
                ('1', 600, 1, 2, '10', 60, 1, 0, 2, 1, 0,
                 'manager', 0, 2, CURRENT_TIMESTAMP),
                ('2', 300, 1, 2, '11', 300, 1, 0, 1, 0, 0,
                 'manager', 0, 1, CURRENT_TIMESTAMP);

            CREATE TABLE conversation_response_intervals (
                chat_id TEXT NOT NULL,
                from_turn_index INTEGER NOT NULL,
                to_turn_index INTEGER NOT NULL,
                transition_type TEXT NOT NULL,
                from_role TEXT NOT NULL,
                to_role TEXT NOT NULL,
                from_actor_id TEXT NOT NULL,
                to_actor_id TEXT NOT NULL,
                manager_user_id TEXT,
                from_last_message_id INTEGER NOT NULL,
                to_first_message_id INTEGER NOT NULL,
                from_last_sent_at TEXT NOT NULL,
                to_first_sent_at TEXT NOT NULL,
                wait_seconds INTEGER NOT NULL,
                is_first_manager_response INTEGER NOT NULL,
                PRIMARY KEY (chat_id, from_turn_index, to_turn_index)
            );

            INSERT INTO conversation_response_intervals VALUES
                ('1', 1, 2, 'client_to_manager', 'client', 'manager',
                 '5000', '10', '10', 1, 2,
                 '2026-08-16T10:00:00+00:00',
                 '2026-08-16T10:01:00+00:00', 60, 1),
                ('1', 2, 3, 'manager_to_client', 'manager', 'client',
                 '10', '5000', '10', 2, 3,
                 '2026-08-16T10:02:00+00:00',
                 '2026-08-16T10:04:00+00:00', 120, 0),
                ('1', 3, 4, 'client_to_manager', 'client', 'manager',
                 '5000', '11', '11', 3, 4,
                 '2026-08-16T10:05:00+00:00',
                 '2026-08-16T10:10:00+00:00', 300, 0),
                ('2', 1, 2, 'client_to_manager', 'client', 'manager',
                 '6000', '11', '11', 5, 6,
                 '2026-08-16T11:00:00+00:00',
                 '2026-08-16T11:05:00+00:00', 300, 1);

            CREATE TABLE conversation_thread_crm_links (
                chat_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL
            );

            INSERT INTO conversation_thread_crm_links VALUES
                ('1', 'lead', '100'),
                ('1', 'deal', '200'),
                ('2', 'lead', '101');
            """
        )
        await db.commit()


@pytest.mark.asyncio
async def test_safe_aggregates_conserve_global_and_manager_totals(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    result = await build_conversation_aggregates(database_path)

    assert result.global_rows == 1
    assert result.manager_rows == 2
    assert result.active_manager_rows == 1
    assert result.inactive_manager_rows == 1
    assert result.channel_rows == 2
    assert result.crm_entity_rows == 3
    assert result.crm_event_link_rows == 7

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT
                response_interval_count,
                client_to_manager_interval_count,
                manager_to_client_interval_count,
                first_response_count
            FROM conversation_global_metrics
            WHERE singleton_id=1
            """
        )
        assert await cursor.fetchone() == (4, 3, 1, 2)

        cursor = await db.execute(
            """
            SELECT
                manager_user_id,
                directory_active,
                response_interval_count,
                first_response_count
            FROM conversation_manager_metrics
            ORDER BY CAST(manager_user_id AS INTEGER)
            """
        )
        assert await cursor.fetchall() == [
            ("10", 1, 1, 1),
            ("11", 0, 2, 1),
        ]


@pytest.mark.asyncio
async def test_crm_aggregate_is_entity_scoped_not_global(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)
    await build_conversation_aggregates(database_path)

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT response_interval_count
            FROM conversation_global_metrics
            WHERE singleton_id=1
            """
        )
        global_intervals = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """
            SELECT SUM(response_interval_count)
            FROM conversation_crm_entity_metrics
            """
        )
        expanded_sum = (await cursor.fetchone())[0]

        assert global_intervals == 4
        assert expanded_sum == 7
        assert expanded_sum > global_intervals


@pytest.mark.asyncio
async def test_aggregate_rebuild_is_idempotent(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    first = await build_conversation_aggregates(database_path)
    second = await build_conversation_aggregates(database_path)

    assert first == second
