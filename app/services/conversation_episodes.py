from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

EPISODE_GAP_SECONDS = 259200


@dataclass(frozen=True, slots=True)
class ConversationEpisodeBuildResult:
    episodes: int
    split_boundaries: int
    multi_episode_chats: int
    mapped_turns: int
    mapped_messages: int
    zero_text_episodes: int


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


async def initialize_conversation_episodes(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_episodes (
                chat_id TEXT NOT NULL,
                episode_index INTEGER NOT NULL,
                split_reason TEXT NOT NULL
                    CHECK (
                        split_reason IN (
                            'chat_start',
                            'inactivity_72h'
                        )
                    ),
                gap_before_seconds INTEGER,
                channel TEXT NOT NULL,
                first_turn_index INTEGER NOT NULL,
                last_turn_index INTEGER NOT NULL,
                first_message_id INTEGER NOT NULL,
                last_message_id INTEGER NOT NULL,
                first_sent_at TEXT NOT NULL,
                last_sent_at TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL
                    CHECK (duration_seconds >= 0),
                human_turn_count INTEGER NOT NULL,
                human_message_count INTEGER NOT NULL,
                text_message_count INTEGER NOT NULL,
                text_chars INTEGER NOT NULL,
                client_turn_count INTEGER NOT NULL,
                manager_turn_count INTEGER NOT NULL,
                client_message_count INTEGER NOT NULL,
                manager_message_count INTEGER NOT NULL,
                distinct_manager_count INTEGER NOT NULL,
                first_role TEXT NOT NULL
                    CHECK (first_role IN ('client', 'manager')),
                last_role TEXT NOT NULL
                    CHECK (last_role IN ('client', 'manager')),
                has_client INTEGER NOT NULL CHECK (has_client IN (0, 1)),
                has_manager INTEGER NOT NULL CHECK (has_manager IN (0, 1)),
                has_dialogue INTEGER NOT NULL CHECK (has_dialogue IN (0, 1)),
                crm_link_count INTEGER NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, episode_index),
                FOREIGN KEY (chat_id)
                    REFERENCES conversation_threads(chat_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_episodes_time
                ON conversation_episodes(first_sent_at, last_sent_at);

            CREATE INDEX IF NOT EXISTS idx_conversation_episodes_channel
                ON conversation_episodes(channel, has_dialogue);

            CREATE INDEX IF NOT EXISTS idx_conversation_episodes_dialogue
                ON conversation_episodes(
                    has_dialogue,
                    has_client,
                    has_manager,
                    chat_id,
                    episode_index
                );

            CREATE TABLE IF NOT EXISTS conversation_episode_turns (
                chat_id TEXT NOT NULL,
                episode_index INTEGER NOT NULL,
                turn_index INTEGER NOT NULL,
                ordinal_in_episode INTEGER NOT NULL,
                gap_before_seconds INTEGER,
                PRIMARY KEY (chat_id, turn_index),
                FOREIGN KEY (chat_id, episode_index)
                    REFERENCES conversation_episodes(chat_id, episode_index)
                    ON DELETE CASCADE,
                FOREIGN KEY (chat_id, turn_index)
                    REFERENCES conversation_turns(chat_id, turn_index)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_episode_turns_episode
                ON conversation_episode_turns(
                    chat_id,
                    episode_index,
                    ordinal_in_episode
                );

            DROP VIEW IF EXISTS conversation_episode_messages;

            CREATE VIEW conversation_episode_messages AS
            SELECT
                episode_turn.chat_id,
                episode_turn.episode_index,
                episode_turn.turn_index,
                episode_turn.ordinal_in_episode,
                turn_message.ordinal_in_turn,
                turn_message.message_id,
                message.sender_role,
                message.sender_id,
                message.sender_directory_user_id,
                message.sent_at,
                message.text_content,
                message.text_sha256,
                message.message_source,
                message.session_binding_kind
            FROM conversation_episode_turns AS episode_turn
            JOIN conversation_turn_messages AS turn_message
              ON turn_message.chat_id = episode_turn.chat_id
             AND turn_message.turn_index = episode_turn.turn_index
            JOIN openlines_messages AS message
              ON message.message_id = turn_message.message_id;

            DROP VIEW IF EXISTS conversation_episode_crm_links;

            CREATE VIEW conversation_episode_crm_links AS
            SELECT
                episode.chat_id,
                episode.episode_index,
                link.entity_type,
                link.entity_id
            FROM conversation_episodes AS episode
            JOIN conversation_thread_crm_links AS link
              ON link.chat_id = episode.chat_id;
            """
        )
        await database.commit()


async def build_conversation_episodes(
    database_path: str,
) -> ConversationEpisodeBuildResult:
    await initialize_conversation_episodes(database_path)

    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.execute("BEGIN IMMEDIATE")

        try:
            await database.execute("DELETE FROM conversation_episode_turns")
            await database.execute("DELETE FROM conversation_episodes")

            await database.executescript(
                """
                DROP TABLE IF EXISTS temp._episode_turn_numbering;

                CREATE TEMP TABLE _episode_turn_numbering AS
                WITH ordered AS (
                    SELECT
                        turn.*,
                        LAG(turn.last_sent_at) OVER (
                            PARTITION BY turn.chat_id
                            ORDER BY turn.turn_index
                        ) AS previous_last_sent_at
                    FROM conversation_turns AS turn
                ),
                gaps AS (
                    SELECT
                        *,
                        CASE
                            WHEN previous_last_sent_at IS NULL
                            THEN NULL
                            ELSE CAST(
                                ROUND(
                                    (
                                        julianday(first_sent_at)
                                        - julianday(previous_last_sent_at)
                                    ) * 86400.0
                                )
                                AS INTEGER
                            )
                        END AS gap_before_seconds
                    FROM ordered
                ),
                boundaries AS (
                    SELECT
                        *,
                        CASE
                            WHEN previous_last_sent_at IS NULL THEN 1
                            WHEN gap_before_seconds >= 259200 THEN 1
                            ELSE 0
                        END AS is_new_episode
                    FROM gaps
                ),
                numbered AS (
                    SELECT
                        *,
                        SUM(is_new_episode) OVER (
                            PARTITION BY chat_id
                            ORDER BY turn_index
                            ROWS BETWEEN UNBOUNDED PRECEDING
                                 AND CURRENT ROW
                        ) AS episode_index
                    FROM boundaries
                )
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY chat_id, episode_index
                        ORDER BY turn_index
                    ) AS ordinal_in_episode
                FROM numbered;

                CREATE INDEX temp.idx_episode_turn_numbering
                    ON _episode_turn_numbering(
                        chat_id,
                        episode_index,
                        turn_index
                    );
                """
            )

            cursor = await database.execute(
                """
                SELECT COUNT(*)
                FROM _episode_turn_numbering
                WHERE gap_before_seconds < 0
                """
            )
            row = await cursor.fetchone()
            negative_gaps = int(row[0]) if row else 0
            if negative_gaps:
                raise RuntimeError(f"Negative human-turn gaps detected: {negative_gaps}")

            await database.execute(
                """
                INSERT INTO conversation_episodes (
                    chat_id,
                    episode_index,
                    split_reason,
                    gap_before_seconds,
                    channel,
                    first_turn_index,
                    last_turn_index,
                    first_message_id,
                    last_message_id,
                    first_sent_at,
                    last_sent_at,
                    duration_seconds,
                    human_turn_count,
                    human_message_count,
                    text_message_count,
                    text_chars,
                    client_turn_count,
                    manager_turn_count,
                    client_message_count,
                    manager_message_count,
                    distinct_manager_count,
                    first_role,
                    last_role,
                    has_client,
                    has_manager,
                    has_dialogue,
                    crm_link_count
                )
                WITH grouped AS (
                    SELECT
                        chat_id,
                        episode_index,
                        MIN(turn_index) AS first_turn_index,
                        MAX(turn_index) AS last_turn_index,
                        MIN(first_message_id) AS first_message_id,
                        MAX(last_message_id) AS last_message_id,
                        COUNT(*) AS human_turn_count,
                        SUM(message_count) AS human_message_count,
                        SUM(text_message_count) AS text_message_count,
                        SUM(text_chars) AS text_chars,
                        SUM(actor_role = 'client') AS client_turn_count,
                        SUM(actor_role = 'manager') AS manager_turn_count,
                        SUM(
                            CASE
                                WHEN actor_role = 'client'
                                THEN message_count
                                ELSE 0
                            END
                        ) AS client_message_count,
                        SUM(
                            CASE
                                WHEN actor_role = 'manager'
                                THEN message_count
                                ELSE 0
                            END
                        ) AS manager_message_count,
                        COUNT(
                            DISTINCT CASE
                                WHEN actor_role = 'manager'
                                THEN manager_user_id
                            END
                        ) AS distinct_manager_count
                    FROM _episode_turn_numbering
                    GROUP BY chat_id, episode_index
                )
                SELECT
                    grouped.chat_id,
                    grouped.episode_index,
                    CASE
                        WHEN grouped.episode_index = 1
                        THEN 'chat_start'
                        ELSE 'inactivity_72h'
                    END,
                    first_turn.gap_before_seconds,
                    COALESCE(
                        NULLIF(TRIM(thread.connector_title), ''),
                        'UNKNOWN'
                    ),
                    grouped.first_turn_index,
                    grouped.last_turn_index,
                    grouped.first_message_id,
                    grouped.last_message_id,
                    first_turn.first_sent_at,
                    last_turn.last_sent_at,
                    CAST(
                        ROUND(
                            (
                                julianday(last_turn.last_sent_at)
                                - julianday(first_turn.first_sent_at)
                            ) * 86400.0
                        )
                        AS INTEGER
                    ),
                    grouped.human_turn_count,
                    grouped.human_message_count,
                    grouped.text_message_count,
                    grouped.text_chars,
                    grouped.client_turn_count,
                    grouped.manager_turn_count,
                    grouped.client_message_count,
                    grouped.manager_message_count,
                    grouped.distinct_manager_count,
                    first_turn.actor_role,
                    last_turn.actor_role,
                    CASE
                        WHEN grouped.client_turn_count > 0 THEN 1
                        ELSE 0
                    END,
                    CASE
                        WHEN grouped.manager_turn_count > 0 THEN 1
                        ELSE 0
                    END,
                    CASE
                        WHEN grouped.client_turn_count > 0
                         AND grouped.manager_turn_count > 0
                        THEN 1
                        ELSE 0
                    END,
                    thread.crm_link_count
                FROM grouped
                JOIN _episode_turn_numbering AS first_turn
                  ON first_turn.chat_id = grouped.chat_id
                 AND first_turn.episode_index = grouped.episode_index
                 AND first_turn.turn_index = grouped.first_turn_index
                JOIN _episode_turn_numbering AS last_turn
                  ON last_turn.chat_id = grouped.chat_id
                 AND last_turn.episode_index = grouped.episode_index
                 AND last_turn.turn_index = grouped.last_turn_index
                JOIN conversation_threads AS thread
                  ON thread.chat_id = grouped.chat_id
                """
            )

            await database.execute(
                """
                INSERT INTO conversation_episode_turns (
                    chat_id,
                    episode_index,
                    turn_index,
                    ordinal_in_episode,
                    gap_before_seconds
                )
                SELECT
                    chat_id,
                    episode_index,
                    turn_index,
                    ordinal_in_episode,
                    gap_before_seconds
                FROM _episode_turn_numbering
                """
            )

            await database.execute("DROP TABLE temp._episode_turn_numbering")
            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return await conversation_episode_status(database_path)


async def conversation_episode_status(
    database_path: str,
) -> ConversationEpisodeBuildResult:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)

        async def scalar(query: str) -> int:
            cursor = await database.execute(query)
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        episodes = await scalar("SELECT COUNT(*) FROM conversation_episodes")
        split_boundaries = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_episodes
            WHERE episode_index > 1
            """
        )
        multi_episode_chats = await scalar(
            """
            SELECT COUNT(*)
            FROM (
                SELECT chat_id
                FROM conversation_episodes
                GROUP BY chat_id
                HAVING COUNT(*) > 1
            )
            """
        )
        mapped_turns = await scalar("SELECT COUNT(*) FROM conversation_episode_turns")
        mapped_messages = await scalar("SELECT COUNT(*) FROM conversation_episode_messages")
        zero_text_episodes = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_episodes
            WHERE text_chars = 0
            """
        )

    return ConversationEpisodeBuildResult(
        episodes=episodes,
        split_boundaries=split_boundaries,
        multi_episode_chats=multi_episode_chats,
        mapped_turns=mapped_turns,
        mapped_messages=mapped_messages,
        zero_text_episodes=zero_text_episodes,
    )
