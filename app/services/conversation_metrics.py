from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True, slots=True)
class ConversationMetricsResult:
    thread_metrics: int
    response_intervals: int
    first_responses: int
    client_to_manager_intervals: int
    manager_to_client_intervals: int
    client_tail_threads: int
    manager_handoffs: int


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


async def initialize_conversation_metrics(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_response_intervals (
                chat_id TEXT NOT NULL,
                from_turn_index INTEGER NOT NULL,
                to_turn_index INTEGER NOT NULL,
                transition_type TEXT NOT NULL
                    CHECK (
                        transition_type IN (
                            'client_to_manager',
                            'manager_to_client'
                        )
                    ),
                from_role TEXT NOT NULL
                    CHECK (from_role IN ('client', 'manager')),
                to_role TEXT NOT NULL
                    CHECK (to_role IN ('client', 'manager')),
                from_actor_id TEXT NOT NULL,
                to_actor_id TEXT NOT NULL,
                manager_user_id TEXT,
                from_last_message_id INTEGER NOT NULL,
                to_first_message_id INTEGER NOT NULL,
                from_last_sent_at TEXT NOT NULL,
                to_first_sent_at TEXT NOT NULL,
                wait_seconds INTEGER NOT NULL CHECK (wait_seconds >= 0),
                is_first_manager_response INTEGER NOT NULL
                    CHECK (is_first_manager_response IN (0, 1)),
                PRIMARY KEY (
                    chat_id,
                    from_turn_index,
                    to_turn_index
                ),
                FOREIGN KEY (chat_id, from_turn_index)
                    REFERENCES conversation_turns(chat_id, turn_index)
                    ON DELETE CASCADE,
                FOREIGN KEY (chat_id, to_turn_index)
                    REFERENCES conversation_turns(chat_id, turn_index)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_response_type
                ON conversation_response_intervals(
                    transition_type,
                    manager_user_id,
                    wait_seconds
                );

            CREATE INDEX IF NOT EXISTS idx_conversation_response_chat
                ON conversation_response_intervals(
                    chat_id,
                    from_turn_index,
                    to_turn_index
                );

            CREATE INDEX IF NOT EXISTS idx_conversation_first_response
                ON conversation_response_intervals(
                    is_first_manager_response,
                    manager_user_id,
                    wait_seconds
                );

            CREATE TABLE IF NOT EXISTS conversation_thread_metrics (
                chat_id TEXT PRIMARY KEY,
                conversation_duration_seconds INTEGER NOT NULL
                    CHECK (conversation_duration_seconds >= 0),
                first_client_turn_index INTEGER,
                first_manager_response_turn_index INTEGER,
                first_manager_response_user_id TEXT,
                first_response_seconds INTEGER
                    CHECK (
                        first_response_seconds IS NULL
                        OR first_response_seconds >= 0
                    ),
                first_response_available INTEGER NOT NULL
                    CHECK (first_response_available IN (0, 1)),
                initial_client_without_manager_response INTEGER NOT NULL
                    CHECK (
                        initial_client_without_manager_response IN (0, 1)
                    ),
                client_to_manager_interval_count INTEGER NOT NULL,
                manager_to_client_interval_count INTEGER NOT NULL,
                manager_handoff_count INTEGER NOT NULL,
                last_human_role TEXT NOT NULL
                    CHECK (last_human_role IN ('client', 'manager')),
                client_tail_after_dialogue INTEGER NOT NULL
                    CHECK (client_tail_after_dialogue IN (0, 1)),
                distinct_manager_count INTEGER NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id)
                    REFERENCES conversation_threads(chat_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_metrics_first_response
                ON conversation_thread_metrics(
                    first_response_available,
                    first_response_seconds,
                    first_manager_response_user_id
                );

            CREATE INDEX IF NOT EXISTS idx_conversation_metrics_client_tail
                ON conversation_thread_metrics(
                    client_tail_after_dialogue,
                    last_human_role,
                    chat_id
                );

            DROP VIEW IF EXISTS conversation_factual_metrics_by_manager;

            CREATE VIEW conversation_factual_metrics_by_manager AS
            SELECT
                interval.manager_user_id,
                COUNT(*) AS client_to_manager_responses,
                AVG(interval.wait_seconds) AS avg_response_seconds,
                MIN(interval.wait_seconds) AS min_response_seconds,
                MAX(interval.wait_seconds) AS max_response_seconds,
                SUM(interval.is_first_manager_response) AS first_responses
            FROM conversation_response_intervals AS interval
            WHERE interval.transition_type = 'client_to_manager'
              AND interval.manager_user_id IS NOT NULL
            GROUP BY interval.manager_user_id;
            """
        )
        await database.commit()


async def build_conversation_metrics(
    database_path: str,
) -> ConversationMetricsResult:
    await initialize_conversation_metrics(database_path)

    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.execute("BEGIN IMMEDIATE")

        try:
            await database.execute("DELETE FROM conversation_response_intervals")
            await database.execute("DELETE FROM conversation_thread_metrics")

            await database.executescript(
                """
                DROP TABLE IF EXISTS temp._conversation_turn_pairs;

                CREATE TEMP TABLE _conversation_turn_pairs AS
                SELECT
                    previous.chat_id,
                    previous.turn_index AS from_turn_index,
                    current.turn_index AS to_turn_index,
                    previous.actor_role AS from_role,
                    current.actor_role AS to_role,
                    previous.actor_id AS from_actor_id,
                    current.actor_id AS to_actor_id,
                    CASE
                        WHEN current.actor_role = 'manager'
                        THEN current.manager_user_id
                        WHEN previous.actor_role = 'manager'
                        THEN previous.manager_user_id
                        ELSE NULL
                    END AS manager_user_id,
                    previous.last_message_id AS from_last_message_id,
                    current.first_message_id AS to_first_message_id,
                    previous.last_sent_at AS from_last_sent_at,
                    current.first_sent_at AS to_first_sent_at,
                    CAST(
                        ROUND(
                            (
                                julianday(current.first_sent_at)
                                - julianday(previous.last_sent_at)
                            ) * 86400.0
                        )
                        AS INTEGER
                    ) AS wait_seconds
                FROM conversation_turns AS previous
                JOIN conversation_turns AS current
                  ON current.chat_id = previous.chat_id
                 AND current.turn_index = previous.turn_index + 1
                WHERE previous.actor_role <> current.actor_role;

                CREATE INDEX temp.idx_conversation_turn_pairs_chat
                    ON _conversation_turn_pairs(chat_id, to_turn_index);
                """
            )

            negative_cursor = await database.execute(
                """
                SELECT COUNT(*)
                FROM _conversation_turn_pairs
                WHERE wait_seconds < 0
                """
            )
            negative_row = await negative_cursor.fetchone()
            negative_count = int(negative_row[0]) if negative_row else 0
            if negative_count:
                raise RuntimeError(f"Negative conversation waits detected: {negative_count}")

            await database.execute(
                """
                INSERT INTO conversation_response_intervals (
                    chat_id,
                    from_turn_index,
                    to_turn_index,
                    transition_type,
                    from_role,
                    to_role,
                    from_actor_id,
                    to_actor_id,
                    manager_user_id,
                    from_last_message_id,
                    to_first_message_id,
                    from_last_sent_at,
                    to_first_sent_at,
                    wait_seconds,
                    is_first_manager_response
                )
                WITH first_client AS (
                    SELECT
                        chat_id,
                        MIN(turn_index) AS first_client_turn_index
                    FROM conversation_turns
                    WHERE actor_role = 'client'
                    GROUP BY chat_id
                ),
                first_manager_after_client AS (
                    SELECT
                        client.chat_id,
                        client.first_client_turn_index,
                        MIN(manager.turn_index) AS first_manager_turn_index
                    FROM first_client AS client
                    JOIN conversation_turns AS manager
                      ON manager.chat_id = client.chat_id
                     AND manager.actor_role = 'manager'
                     AND manager.turn_index > client.first_client_turn_index
                    GROUP BY
                        client.chat_id,
                        client.first_client_turn_index
                )
                SELECT
                    pair.chat_id,
                    pair.from_turn_index,
                    pair.to_turn_index,
                    CASE
                        WHEN pair.from_role = 'client'
                         AND pair.to_role = 'manager'
                        THEN 'client_to_manager'
                        ELSE 'manager_to_client'
                    END,
                    pair.from_role,
                    pair.to_role,
                    pair.from_actor_id,
                    pair.to_actor_id,
                    pair.manager_user_id,
                    pair.from_last_message_id,
                    pair.to_first_message_id,
                    pair.from_last_sent_at,
                    pair.to_first_sent_at,
                    pair.wait_seconds,
                    CASE
                        WHEN pair.from_role = 'client'
                         AND pair.to_role = 'manager'
                         AND first_response.first_manager_turn_index
                             = pair.to_turn_index
                        THEN 1
                        ELSE 0
                    END
                FROM _conversation_turn_pairs AS pair
                LEFT JOIN first_manager_after_client AS first_response
                  ON first_response.chat_id = pair.chat_id
                WHERE pair.wait_seconds >= 0
                """
            )

            await database.execute(
                """
                INSERT INTO conversation_thread_metrics (
                    chat_id,
                    conversation_duration_seconds,
                    first_client_turn_index,
                    first_manager_response_turn_index,
                    first_manager_response_user_id,
                    first_response_seconds,
                    first_response_available,
                    initial_client_without_manager_response,
                    client_to_manager_interval_count,
                    manager_to_client_interval_count,
                    manager_handoff_count,
                    last_human_role,
                    client_tail_after_dialogue,
                    distinct_manager_count
                )
                WITH first_client AS (
                    SELECT
                        chat_id,
                        MIN(turn_index) AS first_client_turn_index
                    FROM conversation_turns
                    WHERE actor_role = 'client'
                    GROUP BY chat_id
                ),
                first_response AS (
                    SELECT
                        interval.chat_id,
                        interval.to_turn_index AS response_turn_index,
                        interval.manager_user_id,
                        interval.wait_seconds
                    FROM conversation_response_intervals AS interval
                    WHERE interval.is_first_manager_response = 1
                ),
                transition_counts AS (
                    SELECT
                        chat_id,
                        SUM(
                            transition_type = 'client_to_manager'
                        ) AS client_to_manager_count,
                        SUM(
                            transition_type = 'manager_to_client'
                        ) AS manager_to_client_count
                    FROM conversation_response_intervals
                    GROUP BY chat_id
                ),
                manager_handoffs AS (
                    SELECT
                        previous.chat_id,
                        COUNT(*) AS handoff_count
                    FROM conversation_turns AS previous
                    JOIN conversation_turns AS current
                      ON current.chat_id = previous.chat_id
                     AND current.turn_index = previous.turn_index + 1
                    WHERE previous.actor_role = 'manager'
                      AND current.actor_role = 'manager'
                      AND previous.actor_id <> current.actor_id
                    GROUP BY previous.chat_id
                )
                SELECT
                    thread.chat_id,
                    CAST(
                        ROUND(
                            (
                                julianday(thread.last_sent_at)
                                - julianday(thread.first_sent_at)
                            ) * 86400.0
                        )
                        AS INTEGER
                    ),
                    first_client.first_client_turn_index,
                    first_response.response_turn_index,
                    first_response.manager_user_id,
                    first_response.wait_seconds,
                    CASE
                        WHEN first_response.response_turn_index IS NOT NULL
                        THEN 1
                        ELSE 0
                    END,
                    CASE
                        WHEN first_client.first_client_turn_index IS NOT NULL
                         AND first_response.response_turn_index IS NULL
                        THEN 1
                        ELSE 0
                    END,
                    COALESCE(
                        transition_counts.client_to_manager_count,
                        0
                    ),
                    COALESCE(
                        transition_counts.manager_to_client_count,
                        0
                    ),
                    COALESCE(manager_handoffs.handoff_count, 0),
                    thread.last_role,
                    thread.client_tail_after_dialogue,
                    thread.distinct_manager_count
                FROM conversation_threads AS thread
                LEFT JOIN first_client
                  ON first_client.chat_id = thread.chat_id
                LEFT JOIN first_response
                  ON first_response.chat_id = thread.chat_id
                LEFT JOIN transition_counts
                  ON transition_counts.chat_id = thread.chat_id
                LEFT JOIN manager_handoffs
                  ON manager_handoffs.chat_id = thread.chat_id
                """
            )

            invalid_duration_cursor = await database.execute(
                """
                SELECT COUNT(*)
                FROM conversation_thread_metrics
                WHERE conversation_duration_seconds < 0
                """
            )
            invalid_duration_row = await invalid_duration_cursor.fetchone()
            invalid_duration_count = int(invalid_duration_row[0]) if invalid_duration_row else 0
            if invalid_duration_count:
                raise RuntimeError(
                    f"Negative conversation durations detected: {invalid_duration_count}"
                )

            await database.execute("DROP TABLE temp._conversation_turn_pairs")
            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return await conversation_metrics_status(database_path)


async def conversation_metrics_status(
    database_path: str,
) -> ConversationMetricsResult:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)

        async def scalar(query: str) -> int:
            cursor = await database.execute(query)
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

        thread_metrics = await scalar("SELECT COUNT(*) FROM conversation_thread_metrics")
        response_intervals = await scalar("SELECT COUNT(*) FROM conversation_response_intervals")
        first_responses = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_response_intervals
            WHERE is_first_manager_response = 1
            """
        )
        client_to_manager_intervals = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_response_intervals
            WHERE transition_type = 'client_to_manager'
            """
        )
        manager_to_client_intervals = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_response_intervals
            WHERE transition_type = 'manager_to_client'
            """
        )
        client_tail_threads = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_thread_metrics
            WHERE client_tail_after_dialogue = 1
            """
        )
        manager_handoffs = await scalar(
            """
            SELECT COALESCE(SUM(manager_handoff_count),0)
            FROM conversation_thread_metrics
            """
        )

    return ConversationMetricsResult(
        thread_metrics=thread_metrics,
        response_intervals=response_intervals,
        first_responses=first_responses,
        client_to_manager_intervals=client_to_manager_intervals,
        manager_to_client_intervals=manager_to_client_intervals,
        client_tail_threads=client_tail_threads,
        manager_handoffs=manager_handoffs,
    )
