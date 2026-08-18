from pathlib import Path

import aiosqlite
import pytest

from app.services.conversation_read_model import build_conversation_read_model


async def _seed_database(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """
            CREATE TABLE openlines_chats (
                chat_id TEXT PRIMARY KEY,
                connector_id TEXT,
                connector_title TEXT
            );

            CREATE TABLE openlines_crm_links (
                chat_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                PRIMARY KEY (chat_id, entity_type, entity_id)
            );

            CREATE TABLE openlines_messages (
                message_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_role TEXT NOT NULL,
                sender_directory_user_id TEXT,
                sent_at TEXT,
                text_content TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                message_source TEXT NOT NULL,
                session_binding_kind TEXT NOT NULL
            );

            INSERT INTO openlines_chats VALUES
                ('1', 'telegram', 'Telegram'),
                ('2', 'whatsapp', 'WhatsApp');

            INSERT INTO openlines_crm_links VALUES
                ('1', 'lead', '100'),
                ('1', 'deal', '200'),
                ('2', 'lead', '300');

            INSERT INTO openlines_messages VALUES
                ('1', '1', 's1', '5000', 'client', NULL,
                 '2026-08-16T10:00:00+00:00', 'one', 'x',
                 'dialog_history', 'chat_history'),
                ('2', '1', 's1', '5000', 'client', NULL,
                 '2026-08-16T10:00:10+00:00', 'two', 'x',
                 'dialog_history', 'chat_history'),
                ('3', '1', 's1', '10', 'manager', '10',
                 '2026-08-16T10:01:00+00:00', 'three', 'x',
                 'dialog_history', 'chat_history'),
                ('4', '1', 's1', '10', 'manager', '10',
                 '2026-08-16T10:01:10+00:00', 'four', 'x',
                 'dialog_history', 'chat_history'),
                ('5', '1', 's1', '11', 'manager', '11',
                 '2026-08-16T10:02:00+00:00', 'five', 'x',
                 'dialog_history', 'chat_history'),
                ('6', '1', 's1', '5000', 'client', NULL,
                 '2026-08-16T10:03:00+00:00', 'six', 'x',
                 'dialog_history', 'chat_history'),
                ('7', '1', 's1', '0', 'system', NULL,
                 '2026-08-16T10:04:00+00:00', 'system', 'x',
                 'dialog_history', 'chat_history'),
                ('8', '2', 's2', '6000', 'client', NULL,
                 '2026-08-16T11:00:00+00:00', 'client only', 'x',
                 'dialog_history', 'chat_history');
            """
        )
        await db.commit()


@pytest.mark.asyncio
async def test_read_model_builds_actor_aware_turns(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed_database(database_path)

    result = await build_conversation_read_model(database_path)

    assert result.threads == 2
    assert result.turns == 5
    assert result.mapped_messages == 7
    assert result.dialogue_threads == 1
    assert result.client_only_threads == 1
    assert result.manager_only_threads == 0
    assert result.client_tail_threads == 1

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT
                turn_index,
                actor_role,
                actor_id,
                manager_user_id,
                first_message_id,
                last_message_id,
                message_count
            FROM conversation_turns
            WHERE chat_id = '1'
            ORDER BY turn_index
            """
        )
        rows = await cursor.fetchall()

        assert rows == [
            (1, "client", "5000", None, 1, 2, 2),
            (2, "manager", "10", "10", 3, 4, 2),
            (3, "manager", "11", "11", 5, 5, 1),
            (4, "client", "5000", None, 6, 6, 1),
        ]

        cursor = await db.execute(
            """
            SELECT
                has_dialogue,
                client_tail_after_dialogue,
                distinct_manager_count,
                crm_link_count
            FROM conversation_threads
            WHERE chat_id = '1'
            """
        )
        assert await cursor.fetchone() == (1, 1, 2, 2)


@pytest.mark.asyncio
async def test_read_model_rebuild_is_idempotent(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed_database(database_path)

    first = await build_conversation_read_model(database_path)
    second = await build_conversation_read_model(database_path)

    assert first == second
