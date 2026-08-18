from pathlib import Path

import aiosqlite
import pytest

from app.services.conversation_episodes import (
    EPISODE_GAP_SECONDS,
    build_conversation_episodes,
)


async def _seed(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """
            CREATE TABLE conversation_threads (
                chat_id TEXT PRIMARY KEY,
                connector_title TEXT,
                crm_link_count INTEGER NOT NULL
            );

            CREATE TABLE conversation_turns (
                chat_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                actor_role TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                manager_user_id TEXT,
                first_message_id INTEGER NOT NULL,
                last_message_id INTEGER NOT NULL,
                first_sent_at TEXT NOT NULL,
                last_sent_at TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                text_message_count INTEGER NOT NULL,
                text_chars INTEGER NOT NULL,
                PRIMARY KEY (chat_id, turn_index),
                FOREIGN KEY (chat_id)
                    REFERENCES conversation_threads(chat_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE conversation_turn_messages (
                message_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                ordinal_in_turn INTEGER NOT NULL
            );

            CREATE TABLE openlines_messages (
                message_id TEXT PRIMARY KEY,
                sender_role TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_directory_user_id TEXT,
                sent_at TEXT NOT NULL,
                text_content TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                message_source TEXT NOT NULL,
                session_binding_kind TEXT NOT NULL
            );

            CREATE TABLE conversation_thread_crm_links (
                chat_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL
            );

            INSERT INTO conversation_threads VALUES
                ('1', 'Telegram', 2),
                ('2', 'WhatsApp', 1);

            INSERT INTO conversation_thread_crm_links VALUES
                ('1', 'lead', '100'),
                ('1', 'deal', '200'),
                ('2', 'lead', '300');

            INSERT INTO conversation_turns VALUES
                (
                    '1', 1, 'client', '5000', NULL, 1, 1,
                    '2026-08-01T10:00:00+00:00',
                    '2026-08-01T10:00:30+00:00',
                    1, 1, 10
                ),
                (
                    '1', 2, 'manager', '10', '10', 2, 2,
                    '2026-08-01T10:01:00+00:00',
                    '2026-08-01T10:02:00+00:00',
                    1, 1, 20
                ),
                (
                    '1', 3, 'client', '5000', NULL, 3, 3,
                    '2026-08-04T10:01:59+00:00',
                    '2026-08-04T10:02:30+00:00',
                    1, 1, 30
                ),
                (
                    '1', 4, 'manager', '11', '11', 4, 4,
                    '2026-08-04T10:03:00+00:00',
                    '2026-08-04T10:04:00+00:00',
                    1, 1, 40
                ),
                (
                    '1', 5, 'client', '5000', NULL, 5, 5,
                    '2026-08-07T10:04:00+00:00',
                    '2026-08-07T10:04:30+00:00',
                    1, 1, 50
                ),
                (
                    '2', 1, 'client', '6000', NULL, 6, 6,
                    '2026-08-02T12:00:00+00:00',
                    '2026-08-02T12:00:00+00:00',
                    1, 0, 0
                );

            INSERT INTO conversation_turn_messages VALUES
                ('1', '1', 1, 1),
                ('2', '1', 2, 1),
                ('3', '1', 3, 1),
                ('4', '1', 4, 1),
                ('5', '1', 5, 1),
                ('6', '2', 1, 1);

            INSERT INTO openlines_messages VALUES
                ('1', 'client', '5000', NULL,
                 '2026-08-01T10:00:00+00:00', 'a', 'h1',
                 'dialog_history', 'chat_history'),
                ('2', 'manager', '10', '10',
                 '2026-08-01T10:01:00+00:00', 'b', 'h2',
                 'dialog_history', 'chat_history'),
                ('3', 'client', '5000', NULL,
                 '2026-08-04T10:01:59+00:00', 'c', 'h3',
                 'dialog_history', 'chat_history'),
                ('4', 'manager', '11', '11',
                 '2026-08-04T10:03:00+00:00', 'd', 'h4',
                 'dialog_history', 'chat_history'),
                ('5', 'client', '5000', NULL,
                 '2026-08-07T10:04:00+00:00', 'e', 'h5',
                 'dialog_history', 'chat_history'),
                ('6', 'client', '6000', NULL,
                 '2026-08-02T12:00:00+00:00', '', 'h6',
                 'dialog_history', 'chat_history');
            """
        )
        await db.commit()


@pytest.mark.asyncio
async def test_episode_boundary_is_72h_or_greater(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    result = await build_conversation_episodes(database_path)

    # Turn 3 starts 71:59:59 after turn 2 ends -> same episode.
    # Turn 5 starts exactly 72h after turn 4 ends -> new episode.
    assert EPISODE_GAP_SECONDS == 259200
    assert result.episodes == 3
    assert result.split_boundaries == 1
    assert result.multi_episode_chats == 1
    assert result.mapped_turns == 6
    assert result.mapped_messages == 6
    assert result.zero_text_episodes == 1

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT
                chat_id,
                episode_index,
                split_reason,
                gap_before_seconds,
                first_turn_index,
                last_turn_index,
                human_turn_count,
                human_message_count,
                text_chars,
                has_dialogue
            FROM conversation_episodes
            ORDER BY CAST(chat_id AS INTEGER), episode_index
            """
        )
        rows = await cursor.fetchall()

        assert rows[0] == ("1", 1, "chat_start", None, 1, 4, 4, 4, 100, 1)
        assert rows[1] == (
            "1",
            2,
            "inactivity_72h",
            259200,
            5,
            5,
            1,
            1,
            50,
            0,
        )
        assert rows[2] == ("2", 1, "chat_start", None, 1, 1, 1, 1, 0, 0)


@pytest.mark.asyncio
async def test_episode_provenance_and_crm_links_are_preserved(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)
    await build_conversation_episodes(database_path)

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM conversation_episode_messages")
        assert (await cursor.fetchone())[0] == 6

        cursor = await db.execute("SELECT COUNT(*) FROM conversation_episode_crm_links")
        # Chat 1 has 2 episodes × 2 CRM links, chat 2 has 1 × 1.
        assert (await cursor.fetchone())[0] == 5


@pytest.mark.asyncio
async def test_episode_rebuild_is_idempotent(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    first = await build_conversation_episodes(database_path)
    second = await build_conversation_episodes(database_path)

    assert first == second
