from pathlib import Path

import aiosqlite
import pytest

from app.services.conversation_metrics import build_conversation_metrics


async def _seed(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """
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

            CREATE TABLE conversation_turns (
                chat_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                actor_role TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                manager_user_id TEXT,
                first_message_id INTEGER NOT NULL,
                last_message_id INTEGER NOT NULL,
                first_sent_at TEXT,
                last_sent_at TEXT,
                message_count INTEGER NOT NULL,
                text_message_count INTEGER NOT NULL,
                text_chars INTEGER NOT NULL,
                PRIMARY KEY (chat_id, turn_index),
                FOREIGN KEY (chat_id)
                    REFERENCES conversation_threads(chat_id)
                    ON DELETE CASCADE
            );

            INSERT INTO conversation_threads (
                chat_id,
                connector_title,
                first_message_id,
                last_message_id,
                first_sent_at,
                last_sent_at,
                human_message_count,
                human_turn_count,
                client_message_count,
                manager_message_count,
                distinct_manager_count,
                first_role,
                last_role,
                has_client,
                has_manager,
                has_dialogue,
                client_tail_after_dialogue,
                crm_link_count
            )
            VALUES
                (
                    '1',
                    'Telegram',
                    1,
                    5,
                    '2026-08-16T10:00:00+00:00',
                    '2026-08-16T10:10:00+00:00',
                    5,
                    5,
                    3,
                    2,
                    2,
                    'client',
                    'client',
                    1,
                    1,
                    1,
                    1,
                    1
                ),
                (
                    '2',
                    'WhatsApp',
                    10,
                    10,
                    '2026-08-16T11:00:00+00:00',
                    '2026-08-16T11:00:00+00:00',
                    1,
                    1,
                    1,
                    0,
                    0,
                    'client',
                    'client',
                    1,
                    0,
                    0,
                    0,
                    1
                );

            INSERT INTO conversation_turns VALUES
                ('1', 1, 'client', '5000', NULL, 1, 1,
                 '2026-08-16T10:00:00+00:00',
                 '2026-08-16T10:00:30+00:00', 1, 1, 10),
                ('1', 2, 'manager', '10', '10', 2, 2,
                 '2026-08-16T10:01:30+00:00',
                 '2026-08-16T10:02:00+00:00', 1, 1, 10),
                ('1', 3, 'client', '5000', NULL, 3, 3,
                 '2026-08-16T10:03:00+00:00',
                 '2026-08-16T10:03:30+00:00', 1, 1, 10),
                ('1', 4, 'manager', '11', '11', 4, 4,
                 '2026-08-16T10:05:00+00:00',
                 '2026-08-16T10:05:30+00:00', 1, 1, 10),
                ('1', 5, 'client', '5000', NULL, 5, 5,
                 '2026-08-16T10:10:00+00:00',
                 '2026-08-16T10:10:00+00:00', 1, 1, 10),
                ('2', 1, 'client', '6000', NULL, 10, 10,
                 '2026-08-16T11:00:00+00:00',
                 '2026-08-16T11:00:00+00:00', 1, 1, 10);
            """
        )
        await db.commit()


@pytest.mark.asyncio
async def test_metrics_materialize_response_intervals(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    result = await build_conversation_metrics(database_path)

    assert result.thread_metrics == 2
    assert result.response_intervals == 4
    assert result.first_responses == 1
    assert result.client_to_manager_intervals == 2
    assert result.manager_to_client_intervals == 2
    assert result.client_tail_threads == 1
    assert result.manager_handoffs == 0

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT
                transition_type,
                manager_user_id,
                wait_seconds,
                is_first_manager_response
            FROM conversation_response_intervals
            WHERE chat_id='1'
            ORDER BY from_turn_index
            """
        )
        rows = await cursor.fetchall()

        assert rows == [
            ("client_to_manager", "10", 60, 1),
            ("manager_to_client", "10", 60, 0),
            ("client_to_manager", "11", 90, 0),
            ("manager_to_client", "11", 270, 0),
        ]

        cursor = await db.execute(
            """
            SELECT
                first_client_turn_index,
                first_manager_response_turn_index,
                first_manager_response_user_id,
                first_response_seconds,
                first_response_available,
                initial_client_without_manager_response,
                client_to_manager_interval_count,
                manager_to_client_interval_count,
                client_tail_after_dialogue
            FROM conversation_thread_metrics
            WHERE chat_id='1'
            """
        )

        assert await cursor.fetchone() == (
            1,
            2,
            "10",
            60,
            1,
            0,
            2,
            2,
            1,
        )


@pytest.mark.asyncio
async def test_client_only_thread_has_missing_first_response_fact(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)
    await build_conversation_metrics(database_path)

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT
                first_client_turn_index,
                first_response_available,
                initial_client_without_manager_response,
                first_response_seconds
            FROM conversation_thread_metrics
            WHERE chat_id='2'
            """
        )

        assert await cursor.fetchone() == (1, 0, 1, None)


@pytest.mark.asyncio
async def test_metrics_rebuild_is_idempotent(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    first = await build_conversation_metrics(database_path)
    second = await build_conversation_metrics(database_path)

    assert first == second
