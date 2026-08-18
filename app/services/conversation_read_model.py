from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True, slots=True)
class ConversationReadModelResult:
    threads: int
    turns: int
    mapped_messages: int
    dialogue_threads: int
    client_only_threads: int
    manager_only_threads: int
    client_tail_threads: int


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


async def initialize_conversation_read_model(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_threads (
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
                first_role TEXT NOT NULL
                    CHECK (first_role IN ('client', 'manager')),
                last_role TEXT NOT NULL
                    CHECK (last_role IN ('client', 'manager')),
                has_client INTEGER NOT NULL CHECK (has_client IN (0, 1)),
                has_manager INTEGER NOT NULL CHECK (has_manager IN (0, 1)),
                has_dialogue INTEGER NOT NULL CHECK (has_dialogue IN (0, 1)),
                client_tail_after_dialogue INTEGER NOT NULL
                    CHECK (client_tail_after_dialogue IN (0, 1)),
                crm_link_count INTEGER NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id)
                    REFERENCES openlines_chats(chat_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_threads_dialogue
                ON conversation_threads(
                    has_dialogue,
                    client_tail_after_dialogue,
                    chat_id
                );

            CREATE INDEX IF NOT EXISTS idx_conversation_threads_channel
                ON conversation_threads(connector_title, chat_id);

            CREATE TABLE IF NOT EXISTS conversation_turns (
                chat_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                actor_role TEXT NOT NULL
                    CHECK (actor_role IN ('client', 'manager')),
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

            CREATE INDEX IF NOT EXISTS idx_conversation_turns_actor
                ON conversation_turns(actor_role, manager_user_id, chat_id);

            CREATE INDEX IF NOT EXISTS idx_conversation_turns_chat_message
                ON conversation_turns(chat_id, first_message_id);

            CREATE TABLE IF NOT EXISTS conversation_turn_messages (
                message_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                ordinal_in_turn INTEGER NOT NULL,
                FOREIGN KEY (chat_id, turn_index)
                    REFERENCES conversation_turns(chat_id, turn_index)
                    ON DELETE CASCADE,
                FOREIGN KEY (message_id)
                    REFERENCES openlines_messages(message_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_turn_messages_turn
                ON conversation_turn_messages(
                    chat_id,
                    turn_index,
                    ordinal_in_turn
                );

            DROP VIEW IF EXISTS conversation_thread_crm_links;

            CREATE VIEW conversation_thread_crm_links AS
            SELECT
                thread.chat_id,
                link.entity_type,
                link.entity_id
            FROM conversation_threads AS thread
            JOIN openlines_crm_links AS link
              ON link.chat_id = thread.chat_id;
            """
        )
        await database.commit()


async def build_conversation_read_model(
    database_path: str,
) -> ConversationReadModelResult:
    await initialize_conversation_read_model(database_path)

    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.execute("BEGIN IMMEDIATE")

        try:
            await database.execute("DELETE FROM conversation_turn_messages")
            await database.execute("DELETE FROM conversation_turns")
            await database.execute("DELETE FROM conversation_threads")

            await database.executescript(
                """
                DROP TABLE IF EXISTS temp._conversation_numbered;

                CREATE TEMP TABLE _conversation_numbered AS
                WITH human AS (
                    SELECT
                        chat_id,
                        message_id,
                        CAST(message_id AS INTEGER) AS message_id_num,
                        sender_role,
                        CASE
                            WHEN sender_role = 'manager'
                            THEN sender_directory_user_id
                            ELSE sender_id
                        END AS actor_id,
                        sender_directory_user_id AS manager_user_id,
                        sent_at,
                        text_content
                    FROM openlines_messages
                    WHERE sender_role IN ('client', 'manager')
                ),
                actor_boundaries AS (
                    SELECT
                        *,
                        CASE
                            WHEN LAG(sender_role) OVER (
                                PARTITION BY chat_id
                                ORDER BY message_id_num
                            ) = sender_role
                            AND LAG(actor_id) OVER (
                                PARTITION BY chat_id
                                ORDER BY message_id_num
                            ) = actor_id
                            THEN 0
                            ELSE 1
                        END AS is_new_turn
                    FROM human
                ),
                turn_numbered AS (
                    SELECT
                        *,
                        SUM(is_new_turn) OVER (
                            PARTITION BY chat_id
                            ORDER BY message_id_num
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                        ) AS turn_index
                    FROM actor_boundaries
                )
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY chat_id, turn_index
                        ORDER BY message_id_num
                    ) AS ordinal_in_turn
                FROM turn_numbered;

                CREATE INDEX temp.idx_conversation_numbered_chat_turn
                    ON _conversation_numbered(
                        chat_id,
                        turn_index,
                        message_id_num
                    );

                CREATE INDEX temp.idx_conversation_numbered_message
                    ON _conversation_numbered(message_id);
                """
            )

            await database.execute(
                """
                INSERT INTO conversation_threads (
                    chat_id,
                    connector_id,
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
                WITH per_chat AS (
                    SELECT
                        chat_id,
                        MIN(message_id_num) AS first_message_id,
                        MAX(message_id_num) AS last_message_id,
                        COUNT(*) AS human_message_count,
                        MAX(turn_index) AS human_turn_count,
                        SUM(sender_role = 'client') AS client_message_count,
                        SUM(sender_role = 'manager') AS manager_message_count,
                        COUNT(
                            DISTINCT CASE
                                WHEN sender_role = 'manager'
                                THEN manager_user_id
                            END
                        ) AS distinct_manager_count
                    FROM _conversation_numbered
                    GROUP BY chat_id
                ),
                boundary_roles AS (
                    SELECT
                        numbered.chat_id,
                        MAX(
                            CASE
                                WHEN numbered.message_id_num = per_chat.first_message_id
                                THEN numbered.sender_role
                            END
                        ) AS first_role,
                        MAX(
                            CASE
                                WHEN numbered.message_id_num = per_chat.last_message_id
                                THEN numbered.sender_role
                            END
                        ) AS last_role,
                        MAX(
                            CASE
                                WHEN numbered.message_id_num = per_chat.first_message_id
                                THEN numbered.sent_at
                            END
                        ) AS first_sent_at,
                        MAX(
                            CASE
                                WHEN numbered.message_id_num = per_chat.last_message_id
                                THEN numbered.sent_at
                            END
                        ) AS last_sent_at
                    FROM _conversation_numbered AS numbered
                    JOIN per_chat
                      ON per_chat.chat_id = numbered.chat_id
                    GROUP BY numbered.chat_id
                ),
                crm_counts AS (
                    SELECT
                        chat_id,
                        COUNT(*) AS crm_link_count
                    FROM openlines_crm_links
                    GROUP BY chat_id
                )
                SELECT
                    per_chat.chat_id,
                    chat.connector_id,
                    chat.connector_title,
                    per_chat.first_message_id,
                    per_chat.last_message_id,
                    boundary_roles.first_sent_at,
                    boundary_roles.last_sent_at,
                    per_chat.human_message_count,
                    per_chat.human_turn_count,
                    per_chat.client_message_count,
                    per_chat.manager_message_count,
                    per_chat.distinct_manager_count,
                    boundary_roles.first_role,
                    boundary_roles.last_role,
                    CASE
                        WHEN per_chat.client_message_count > 0 THEN 1
                        ELSE 0
                    END,
                    CASE
                        WHEN per_chat.manager_message_count > 0 THEN 1
                        ELSE 0
                    END,
                    CASE
                        WHEN per_chat.client_message_count > 0
                         AND per_chat.manager_message_count > 0
                        THEN 1
                        ELSE 0
                    END,
                    CASE
                        WHEN per_chat.client_message_count > 0
                         AND per_chat.manager_message_count > 0
                         AND boundary_roles.last_role = 'client'
                        THEN 1
                        ELSE 0
                    END,
                    COALESCE(crm_counts.crm_link_count, 0)
                FROM per_chat
                JOIN boundary_roles
                  ON boundary_roles.chat_id = per_chat.chat_id
                JOIN openlines_chats AS chat
                  ON chat.chat_id = per_chat.chat_id
                LEFT JOIN crm_counts
                  ON crm_counts.chat_id = per_chat.chat_id
                """
            )

            await database.execute(
                """
                INSERT INTO conversation_turns (
                    chat_id,
                    turn_index,
                    actor_role,
                    actor_id,
                    manager_user_id,
                    first_message_id,
                    last_message_id,
                    first_sent_at,
                    last_sent_at,
                    message_count,
                    text_message_count,
                    text_chars
                )
                WITH grouped AS (
                    SELECT
                        chat_id,
                        turn_index,
                        sender_role,
                        actor_id,
                        CASE
                            WHEN sender_role = 'manager' THEN manager_user_id
                            ELSE NULL
                        END AS manager_user_id,
                        MIN(message_id_num) AS first_message_id,
                        MAX(message_id_num) AS last_message_id,
                        COUNT(*) AS message_count,
                        SUM(TRIM(COALESCE(text_content, '')) <> '') AS text_message_count,
                        SUM(LENGTH(COALESCE(text_content, ''))) AS text_chars
                    FROM _conversation_numbered
                    GROUP BY
                        chat_id,
                        turn_index,
                        sender_role,
                        actor_id,
                        CASE
                            WHEN sender_role = 'manager' THEN manager_user_id
                            ELSE NULL
                        END
                )
                SELECT
                    grouped.chat_id,
                    grouped.turn_index,
                    grouped.sender_role,
                    grouped.actor_id,
                    grouped.manager_user_id,
                    grouped.first_message_id,
                    grouped.last_message_id,
                    first_row.sent_at,
                    last_row.sent_at,
                    grouped.message_count,
                    grouped.text_message_count,
                    grouped.text_chars
                FROM grouped
                JOIN _conversation_numbered AS first_row
                  ON first_row.chat_id = grouped.chat_id
                 AND first_row.message_id_num = grouped.first_message_id
                JOIN _conversation_numbered AS last_row
                  ON last_row.chat_id = grouped.chat_id
                 AND last_row.message_id_num = grouped.last_message_id
                """
            )

            await database.execute(
                """
                INSERT INTO conversation_turn_messages (
                    message_id,
                    chat_id,
                    turn_index,
                    ordinal_in_turn
                )
                SELECT
                    message_id,
                    chat_id,
                    turn_index,
                    ordinal_in_turn
                FROM _conversation_numbered
                """
            )

            await database.execute("DROP TABLE temp._conversation_numbered")
            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return await conversation_read_model_status(database_path)


async def conversation_read_model_status(
    database_path: str,
) -> ConversationReadModelResult:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)

        async def scalar(query: str) -> int:
            cursor = await database.execute(query)
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

        threads = await scalar("SELECT COUNT(*) FROM conversation_threads")
        turns = await scalar("SELECT COUNT(*) FROM conversation_turns")
        mapped_messages = await scalar("SELECT COUNT(*) FROM conversation_turn_messages")
        dialogue_threads = await scalar(
            "SELECT COUNT(*) FROM conversation_threads WHERE has_dialogue = 1"
        )
        client_only_threads = await scalar(
            "SELECT COUNT(*) FROM conversation_threads WHERE has_client = 1 AND has_manager = 0"
        )
        manager_only_threads = await scalar(
            "SELECT COUNT(*) FROM conversation_threads WHERE has_manager = 1 AND has_client = 0"
        )
        client_tail_threads = await scalar(
            "SELECT COUNT(*) FROM conversation_threads WHERE client_tail_after_dialogue = 1"
        )

    return ConversationReadModelResult(
        threads=threads,
        turns=turns,
        mapped_messages=mapped_messages,
        dialogue_threads=dialogue_threads,
        client_only_threads=client_only_threads,
        manager_only_threads=manager_only_threads,
        client_tail_threads=client_tail_threads,
    )
