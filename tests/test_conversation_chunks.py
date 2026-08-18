from pathlib import Path

import aiosqlite
import pytest

from app.services.conversation_chunks import (
    MAX_CHUNK_TEXT_CHARS,
    build_conversation_chunks,
)


async def _seed(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(
            """
            CREATE TABLE conversation_episodes (
                chat_id TEXT NOT NULL,
                episode_index INTEGER NOT NULL,
                PRIMARY KEY (chat_id, episode_index)
            );

            CREATE TABLE openlines_messages (
                message_id TEXT PRIMARY KEY,
                sender_role TEXT NOT NULL,
                sender_directory_user_id TEXT,
                sent_at TEXT NOT NULL,
                text_content TEXT NOT NULL,
                text_sha256 TEXT NOT NULL
            );

            CREATE TABLE conversation_episode_messages (
                chat_id TEXT NOT NULL,
                episode_index INTEGER NOT NULL,
                turn_index INTEGER NOT NULL,
                ordinal_in_episode INTEGER NOT NULL,
                ordinal_in_turn INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                sender_role TEXT NOT NULL,
                sender_directory_user_id TEXT,
                sent_at TEXT NOT NULL,
                text_content TEXT NOT NULL,
                text_sha256 TEXT NOT NULL
            );

            CREATE TABLE conversation_episode_crm_links (
                chat_id TEXT NOT NULL,
                episode_index INTEGER NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL
            );

            INSERT INTO conversation_episodes VALUES
                ('1', 1),
                ('2', 1);

            INSERT INTO conversation_episode_crm_links VALUES
                ('1', 1, 'lead', '100'),
                ('2', 1, 'deal', '200');
            """
        )

        normal_a = "a" * 6000
        normal_b = "b" * 5000
        huge = "x" * 25000

        rows = [
            (
                "1",
                1,
                1,
                1,
                1,
                "1",
                "client",
                None,
                "2026-08-16T10:00:00+00:00",
                normal_a,
                "h1",
            ),
            (
                "1",
                1,
                2,
                2,
                1,
                "2",
                "manager",
                "10",
                "2026-08-16T10:01:00+00:00",
                normal_b,
                "h2",
            ),
            (
                "1",
                1,
                3,
                3,
                1,
                "3",
                "client",
                None,
                "2026-08-16T10:02:00+00:00",
                huge,
                "h3",
            ),
            (
                "2",
                1,
                1,
                1,
                1,
                "4",
                "client",
                None,
                "2026-08-16T11:00:00+00:00",
                "",
                "h4",
            ),
        ]

        await db.executemany(
            """
            INSERT INTO conversation_episode_messages (
                chat_id,
                episode_index,
                turn_index,
                ordinal_in_episode,
                ordinal_in_turn,
                message_id,
                sender_role,
                sender_directory_user_id,
                sent_at,
                text_content,
                text_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await db.executemany(
            """
            INSERT INTO openlines_messages (
                message_id,
                sender_role,
                sender_directory_user_id,
                sent_at,
                text_content,
                text_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                )
                for row in rows
            ],
        )
        await db.commit()


@pytest.mark.asyncio
async def test_chunker_preserves_normal_messages_and_splits_only_huge_message(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    result = await build_conversation_chunks(database_path)

    assert MAX_CHUNK_TEXT_CHARS == 10000
    assert result.episodes == 2
    assert result.chunks == 6
    assert result.text_chunks == 5
    assert result.zero_text_chunks == 1
    assert result.split_episodes == 1
    assert result.max_chunks_per_episode == 5
    assert result.raw_messages == 4
    assert result.distinct_mapped_messages == 4
    assert result.segments == 6
    assert result.split_messages == 1
    assert result.total_text_chars == 36000
    assert result.max_chunk_text_chars == 10000

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT
                chunk_index,
                first_message_id,
                last_message_id,
                text_chars,
                starts_mid_turn,
                ends_mid_turn,
                split_message_segment_count
            FROM conversation_semantic_chunks
            WHERE chat_id='1' AND episode_index=1
            ORDER BY chunk_index
            """
        )
        rows = await cursor.fetchall()

        assert rows == [
            (1, "1", "1", 6000, 0, 0, 0),
            (2, "2", "2", 5000, 0, 0, 0),
            (3, "3", "3", 10000, 0, 1, 1),
            (4, "3", "3", 10000, 1, 1, 1),
            (5, "3", "3", 5000, 1, 0, 1),
        ]


@pytest.mark.asyncio
async def test_chunk_segments_preserve_exact_message_offsets(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)
    await build_conversation_chunks(database_path)

    async with aiosqlite.connect(database_path) as db:
        cursor = await db.execute(
            """
            SELECT
                char_start,
                char_end,
                segment_text_chars,
                message_segment_index,
                message_segment_count
            FROM conversation_semantic_chunk_segments
            WHERE message_id='3'
            ORDER BY message_segment_index
            """
        )

        assert await cursor.fetchall() == [
            (0, 10000, 10000, 1, 3),
            (10000, 20000, 10000, 2, 3),
            (20000, 25000, 5000, 3, 3),
        ]


@pytest.mark.asyncio
async def test_chunk_rebuild_is_idempotent(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed(database_path)

    first = await build_conversation_chunks(database_path)
    second = await build_conversation_chunks(database_path)

    assert first == second
