from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import aiosqlite

MAX_CHUNK_TEXT_CHARS = 10000


@dataclass(frozen=True, slots=True)
class ConversationChunkBuildResult:
    episodes: int
    chunks: int
    text_chunks: int
    zero_text_chunks: int
    split_episodes: int
    max_chunks_per_episode: int
    raw_messages: int
    distinct_mapped_messages: int
    segments: int
    split_messages: int
    total_text_chars: int
    max_chunk_text_chars: int
    duplicate_content_fingerprints: int
    reusable_duplicate_chunks: int


@dataclass(frozen=True, slots=True)
class _Message:
    chat_id: str
    episode_index: int
    turn_index: int
    ordinal_in_turn: int
    message_id: str
    sender_role: str
    manager_user_id: str | None
    sent_at: str
    text_content: str
    text_sha256: str


@dataclass(frozen=True, slots=True)
class _Segment:
    message: _Message
    char_start: int
    char_end: int
    segment_text: str
    message_segment_index: int
    message_segment_count: int


@dataclass(frozen=True, slots=True)
class _Chunk:
    segments: tuple[_Segment, ...]
    starts_mid_turn: bool
    ends_mid_turn: bool


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


async def initialize_conversation_chunks(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_semantic_chunks (
                chat_id TEXT NOT NULL,
                episode_index INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                split_reason TEXT NOT NULL
                    CHECK (
                        split_reason IN (
                            'episode_start',
                            'size_limit'
                        )
                    ),
                first_message_id TEXT NOT NULL,
                last_message_id TEXT NOT NULL,
                first_sent_at TEXT NOT NULL,
                last_sent_at TEXT NOT NULL,
                first_role TEXT NOT NULL
                    CHECK (first_role IN ('client', 'manager')),
                last_role TEXT NOT NULL
                    CHECK (last_role IN ('client', 'manager')),
                segment_count INTEGER NOT NULL,
                distinct_message_count INTEGER NOT NULL,
                text_message_count INTEGER NOT NULL,
                text_chars INTEGER NOT NULL,
                client_segment_count INTEGER NOT NULL,
                manager_segment_count INTEGER NOT NULL,
                distinct_manager_count INTEGER NOT NULL,
                starts_mid_turn INTEGER NOT NULL
                    CHECK (starts_mid_turn IN (0, 1)),
                ends_mid_turn INTEGER NOT NULL
                    CHECK (ends_mid_turn IN (0, 1)),
                split_message_segment_count INTEGER NOT NULL,
                source_fingerprint_sha256 TEXT NOT NULL,
                content_fingerprint_sha256 TEXT NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, episode_index, chunk_index),
                FOREIGN KEY (chat_id, episode_index)
                    REFERENCES conversation_episodes(chat_id, episode_index)
                    ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_chunk_source_hash
                ON conversation_semantic_chunks(source_fingerprint_sha256);

            CREATE INDEX IF NOT EXISTS idx_semantic_chunk_content_hash
                ON conversation_semantic_chunks(content_fingerprint_sha256);

            CREATE INDEX IF NOT EXISTS idx_semantic_chunk_text_size
                ON conversation_semantic_chunks(text_chars, chunk_index);

            CREATE TABLE IF NOT EXISTS conversation_semantic_chunk_segments (
                chat_id TEXT NOT NULL,
                episode_index INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                segment_index INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                ordinal_in_turn INTEGER NOT NULL,
                sender_role TEXT NOT NULL
                    CHECK (sender_role IN ('client', 'manager')),
                manager_user_id TEXT,
                sent_at TEXT NOT NULL,
                char_start INTEGER NOT NULL CHECK (char_start >= 0),
                char_end INTEGER NOT NULL CHECK (char_end >= char_start),
                segment_text_chars INTEGER NOT NULL
                    CHECK (segment_text_chars >= 0),
                message_segment_index INTEGER NOT NULL,
                message_segment_count INTEGER NOT NULL,
                segment_text_sha256 TEXT NOT NULL,
                PRIMARY KEY (
                    chat_id,
                    episode_index,
                    chunk_index,
                    segment_index
                ),
                FOREIGN KEY (chat_id, episode_index, chunk_index)
                    REFERENCES conversation_semantic_chunks(
                        chat_id,
                        episode_index,
                        chunk_index
                    )
                    ON DELETE CASCADE,
                FOREIGN KEY (message_id)
                    REFERENCES openlines_messages(message_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_semantic_chunk_segment_message
                ON conversation_semantic_chunk_segments(
                    message_id,
                    message_segment_index
                );

            CREATE INDEX IF NOT EXISTS idx_semantic_chunk_segment_episode
                ON conversation_semantic_chunk_segments(
                    chat_id,
                    episode_index,
                    chunk_index,
                    segment_index
                );

            DROP VIEW IF EXISTS conversation_semantic_chunk_crm_links;

            CREATE VIEW conversation_semantic_chunk_crm_links AS
            SELECT
                chunk.chat_id,
                chunk.episode_index,
                chunk.chunk_index,
                link.entity_type,
                link.entity_id
            FROM conversation_semantic_chunks AS chunk
            JOIN conversation_episode_crm_links AS link
              ON link.chat_id = chunk.chat_id
             AND link.episode_index = chunk.episode_index;
            """
        )
        await database.commit()


def _split_message(message: _Message) -> tuple[_Segment, ...]:
    text = message.text_content
    if not text:
        return (
            _Segment(
                message=message,
                char_start=0,
                char_end=0,
                segment_text="",
                message_segment_index=1,
                message_segment_count=1,
            ),
        )

    if len(text) <= MAX_CHUNK_TEXT_CHARS:
        return (
            _Segment(
                message=message,
                char_start=0,
                char_end=len(text),
                segment_text=text,
                message_segment_index=1,
                message_segment_count=1,
            ),
        )

    parts = [
        text[start : start + MAX_CHUNK_TEXT_CHARS]
        for start in range(0, len(text), MAX_CHUNK_TEXT_CHARS)
    ]
    count = len(parts)
    result: list[_Segment] = []
    offset = 0

    for index, part in enumerate(parts, start=1):
        start = offset
        end = start + len(part)
        result.append(
            _Segment(
                message=message,
                char_start=start,
                char_end=end,
                segment_text=part,
                message_segment_index=index,
                message_segment_count=count,
            )
        )
        offset = end

    return tuple(result)


def _chunk_episode(messages: list[_Message]) -> tuple[_Chunk, ...]:
    raw_chunks: list[list[_Segment]] = []
    current: list[_Segment] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if current:
            raw_chunks.append(current)
            current = []
            current_chars = 0

    for message in messages:
        segments = _split_message(message)

        # A normal-sized message is never split merely because the current
        # chunk has too little space: flush first and keep the message whole.
        if len(segments) == 1:
            segment = segments[0]
            size = len(segment.segment_text)
            if current and current_chars + size > MAX_CHUNK_TEXT_CHARS:
                flush()
            current.append(segment)
            current_chars += size
            continue

        # An intrinsically oversized message is the only case where one raw
        # message is split. Preserve each segment and exact character offsets.
        flush()
        for segment in segments:
            raw_chunks.append([segment])

    flush()

    chunks: list[_Chunk] = []
    for index, segments in enumerate(raw_chunks):
        previous = raw_chunks[index - 1] if index > 0 else None
        following = raw_chunks[index + 1] if index + 1 < len(raw_chunks) else None

        starts_mid_turn = bool(
            previous and previous[-1].message.turn_index == segments[0].message.turn_index
        )
        ends_mid_turn = bool(
            following and segments[-1].message.turn_index == following[0].message.turn_index
        )

        chunks.append(
            _Chunk(
                segments=tuple(segments),
                starts_mid_turn=starts_mid_turn,
                ends_mid_turn=ends_mid_turn,
            )
        )

    return tuple(chunks)


def _chunk_fingerprints(chunk: _Chunk) -> tuple[str, str]:
    source = hashlib.sha256()
    content = hashlib.sha256()

    for segment in chunk.segments:
        message = segment.message
        segment_hash = hashlib.sha256(segment.segment_text.encode("utf-8")).hexdigest()

        source.update(
            (
                f"{message.message_id}|{segment.char_start}|"
                f"{segment.char_end}|{message.sender_role}|"
                f"{message.text_sha256}\n"
            ).encode()
        )

        if segment.segment_text:
            content_material = f"{message.sender_role}|{segment_hash}\n"
        else:
            # Non-text content can represent different files/attachments.
            # Do not declare two such chunks reusable merely because both
            # have an empty text body.
            content_material = f"{message.sender_role}|NON_TEXT|{message.message_id}\n"
        content.update(content_material.encode("utf-8"))

    return source.hexdigest(), content.hexdigest()


async def build_conversation_chunks(
    database_path: str,
) -> ConversationChunkBuildResult:
    await initialize_conversation_chunks(database_path)

    async with aiosqlite.connect(database_path) as database:
        database.row_factory = aiosqlite.Row
        await _prepare_connection(database)
        await database.execute("BEGIN IMMEDIATE")

        try:
            await database.execute("DELETE FROM conversation_semantic_chunk_segments")
            await database.execute("DELETE FROM conversation_semantic_chunks")

            cursor = await database.execute(
                """
                SELECT
                    message.chat_id,
                    message.episode_index,
                    message.turn_index,
                    message.ordinal_in_turn,
                    message.message_id,
                    message.sender_role,
                    message.sender_directory_user_id,
                    message.sent_at,
                    message.text_content,
                    message.text_sha256
                FROM conversation_episode_messages AS message
                ORDER BY
                    CAST(message.chat_id AS INTEGER),
                    message.episode_index,
                    message.ordinal_in_episode,
                    message.ordinal_in_turn,
                    CAST(message.message_id AS INTEGER)
                """
            )

            chunk_rows: list[tuple[Any, ...]] = []
            segment_rows: list[tuple[Any, ...]] = []

            current_key: tuple[str, int] | None = None
            episode_messages: list[_Message] = []
            split_messages: set[str] = set()

            def materialize_episode(
                key: tuple[str, int],
                messages: list[_Message],
            ) -> None:
                chunks = _chunk_episode(messages)

                for chunk_index, chunk in enumerate(chunks, start=1):
                    segments = chunk.segments
                    first = segments[0].message
                    last = segments[-1].message
                    source_hash, content_hash = _chunk_fingerprints(chunk)

                    distinct_message_ids = {segment.message.message_id for segment in segments}
                    text_message_ids = {
                        segment.message.message_id
                        for segment in segments
                        if segment.message.text_content
                    }
                    managers = {
                        segment.message.manager_user_id
                        for segment in segments
                        if segment.message.sender_role == "manager"
                        and segment.message.manager_user_id is not None
                    }

                    text_chars = sum(len(segment.segment_text) for segment in segments)
                    split_segment_count = sum(
                        1 for segment in segments if segment.message_segment_count > 1
                    )

                    chunk_rows.append(
                        (
                            key[0],
                            key[1],
                            chunk_index,
                            ("episode_start" if chunk_index == 1 else "size_limit"),
                            first.message_id,
                            last.message_id,
                            first.sent_at,
                            last.sent_at,
                            first.sender_role,
                            last.sender_role,
                            len(segments),
                            len(distinct_message_ids),
                            len(text_message_ids),
                            text_chars,
                            sum(
                                1 for segment in segments if segment.message.sender_role == "client"
                            ),
                            sum(
                                1
                                for segment in segments
                                if segment.message.sender_role == "manager"
                            ),
                            len(managers),
                            int(chunk.starts_mid_turn),
                            int(chunk.ends_mid_turn),
                            split_segment_count,
                            source_hash,
                            content_hash,
                        )
                    )

                    for segment_index, segment in enumerate(
                        segments,
                        start=1,
                    ):
                        if segment.message_segment_count > 1:
                            split_messages.add(segment.message.message_id)

                        segment_hash = hashlib.sha256(
                            segment.segment_text.encode("utf-8")
                        ).hexdigest()

                        segment_rows.append(
                            (
                                key[0],
                                key[1],
                                chunk_index,
                                segment_index,
                                segment.message.message_id,
                                segment.message.turn_index,
                                segment.message.ordinal_in_turn,
                                segment.message.sender_role,
                                segment.message.manager_user_id,
                                segment.message.sent_at,
                                segment.char_start,
                                segment.char_end,
                                len(segment.segment_text),
                                segment.message_segment_index,
                                segment.message_segment_count,
                                segment_hash,
                            )
                        )

            async for row in cursor:
                key = (str(row["chat_id"]), int(row["episode_index"]))
                if current_key is not None and key != current_key:
                    materialize_episode(
                        current_key,
                        episode_messages,
                    )
                    episode_messages = []

                current_key = key
                episode_messages.append(
                    _Message(
                        chat_id=key[0],
                        episode_index=key[1],
                        turn_index=int(row["turn_index"]),
                        ordinal_in_turn=int(row["ordinal_in_turn"]),
                        message_id=str(row["message_id"]),
                        sender_role=str(row["sender_role"]),
                        manager_user_id=(
                            str(row["sender_directory_user_id"])
                            if row["sender_directory_user_id"] is not None
                            else None
                        ),
                        sent_at=str(row["sent_at"]),
                        text_content=str(row["text_content"] or ""),
                        text_sha256=str(row["text_sha256"] or ""),
                    )
                )

            if current_key is not None:
                materialize_episode(current_key, episode_messages)

            await database.executemany(
                """
                INSERT INTO conversation_semantic_chunks (
                    chat_id,
                    episode_index,
                    chunk_index,
                    split_reason,
                    first_message_id,
                    last_message_id,
                    first_sent_at,
                    last_sent_at,
                    first_role,
                    last_role,
                    segment_count,
                    distinct_message_count,
                    text_message_count,
                    text_chars,
                    client_segment_count,
                    manager_segment_count,
                    distinct_manager_count,
                    starts_mid_turn,
                    ends_mid_turn,
                    split_message_segment_count,
                    source_fingerprint_sha256,
                    content_fingerprint_sha256
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                chunk_rows,
            )

            await database.executemany(
                """
                INSERT INTO conversation_semantic_chunk_segments (
                    chat_id,
                    episode_index,
                    chunk_index,
                    segment_index,
                    message_id,
                    turn_index,
                    ordinal_in_turn,
                    sender_role,
                    manager_user_id,
                    sent_at,
                    char_start,
                    char_end,
                    segment_text_chars,
                    message_segment_index,
                    message_segment_count,
                    segment_text_sha256
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                segment_rows,
            )

            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return await conversation_chunk_status(database_path)


async def conversation_chunk_status(
    database_path: str,
) -> ConversationChunkBuildResult:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)

        async def scalar(query: str) -> int:
            cursor = await database.execute(query)
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        episodes = await scalar("SELECT COUNT(*) FROM conversation_episodes")
        chunks = await scalar("SELECT COUNT(*) FROM conversation_semantic_chunks")
        text_chunks = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_semantic_chunks
            WHERE text_chars > 0
            """
        )
        zero_text_chunks = chunks - text_chunks
        split_episodes = await scalar(
            """
            SELECT COUNT(*)
            FROM (
                SELECT chat_id, episode_index
                FROM conversation_semantic_chunks
                GROUP BY chat_id, episode_index
                HAVING COUNT(*) > 1
            )
            """
        )
        max_chunks_per_episode = await scalar(
            """
            SELECT COALESCE(MAX(chunk_count),0)
            FROM (
                SELECT
                    chat_id,
                    episode_index,
                    COUNT(*) AS chunk_count
                FROM conversation_semantic_chunks
                GROUP BY chat_id, episode_index
            )
            """
        )
        raw_messages = await scalar("SELECT COUNT(*) FROM conversation_episode_messages")
        distinct_mapped_messages = await scalar(
            """
            SELECT COUNT(DISTINCT message_id)
            FROM conversation_semantic_chunk_segments
            """
        )
        segments = await scalar("SELECT COUNT(*) FROM conversation_semantic_chunk_segments")
        split_messages = await scalar(
            """
            SELECT COUNT(DISTINCT message_id)
            FROM conversation_semantic_chunk_segments
            WHERE message_segment_count > 1
            """
        )
        total_text_chars = await scalar(
            """
            SELECT COALESCE(SUM(segment_text_chars),0)
            FROM conversation_semantic_chunk_segments
            """
        )
        max_chunk_text_chars = await scalar(
            """
            SELECT COALESCE(MAX(text_chars),0)
            FROM conversation_semantic_chunks
            """
        )
        duplicate_content_fingerprints = await scalar(
            """
            SELECT COUNT(*)
            FROM (
                SELECT content_fingerprint_sha256
                FROM conversation_semantic_chunks
                WHERE text_chars > 0
                GROUP BY content_fingerprint_sha256
                HAVING COUNT(*) > 1
            )
            """
        )
        reusable_duplicate_chunks = await scalar(
            """
            SELECT COALESCE(SUM(chunk_count - 1),0)
            FROM (
                SELECT
                    content_fingerprint_sha256,
                    COUNT(*) AS chunk_count
                FROM conversation_semantic_chunks
                WHERE text_chars > 0
                GROUP BY content_fingerprint_sha256
                HAVING COUNT(*) > 1
            )
            """
        )

    return ConversationChunkBuildResult(
        episodes=episodes,
        chunks=chunks,
        text_chunks=text_chunks,
        zero_text_chunks=zero_text_chunks,
        split_episodes=split_episodes,
        max_chunks_per_episode=max_chunks_per_episode,
        raw_messages=raw_messages,
        distinct_mapped_messages=distinct_mapped_messages,
        segments=segments,
        split_messages=split_messages,
        total_text_chars=total_text_chars,
        max_chunk_text_chars=max_chunk_text_chars,
        duplicate_content_fingerprints=duplicate_content_fingerprints,
        reusable_duplicate_chunks=reusable_duplicate_chunks,
    )
