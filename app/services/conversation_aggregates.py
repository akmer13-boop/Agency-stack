from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil
from typing import Any

import aiosqlite


@dataclass(frozen=True, slots=True)
class ConversationAggregatesResult:
    global_rows: int
    manager_rows: int
    active_manager_rows: int
    inactive_manager_rows: int
    channel_rows: int
    crm_entity_rows: int
    crm_event_link_rows: int


def _median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    size = len(ordered)
    middle = size // 2
    if size % 2:
        return int(ordered[middle])
    return int(round((ordered[middle - 1] + ordered[middle]) / 2))


def _p90(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, ceil(0.90 * len(ordered)))
    return int(ordered[rank - 1])


def _directory_active(payload_json: str | None) -> int:
    try:
        payload: dict[str, Any] = json.loads(payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}

    value = payload.get("ACTIVE", True)
    if value in (False, 0, "0", "N", "n", "false", "False"):
        return 0
    return 1


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


async def initialize_conversation_aggregates(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_global_metrics (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                thread_count INTEGER NOT NULL,
                client_thread_count INTEGER NOT NULL,
                dialogue_thread_count INTEGER NOT NULL,
                response_interval_count INTEGER NOT NULL,
                client_to_manager_interval_count INTEGER NOT NULL,
                manager_to_client_interval_count INTEGER NOT NULL,
                first_response_count INTEGER NOT NULL,
                initial_client_without_manager_response_count INTEGER NOT NULL,
                client_tail_thread_count INTEGER NOT NULL,
                manager_handoff_count INTEGER NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversation_manager_metrics (
                manager_user_id TEXT PRIMARY KEY,
                directory_active INTEGER NOT NULL
                    CHECK (directory_active IN (0, 1)),
                response_interval_count INTEGER NOT NULL,
                response_chat_count INTEGER NOT NULL,
                first_response_count INTEGER NOT NULL,
                response_median_seconds INTEGER,
                response_p90_seconds INTEGER,
                first_response_median_seconds INTEGER,
                first_response_p90_seconds INTEGER,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_manager_active
                ON conversation_manager_metrics(
                    directory_active,
                    response_interval_count DESC
                );

            CREATE TABLE IF NOT EXISTS conversation_channel_metrics (
                channel TEXT PRIMARY KEY,
                thread_count INTEGER NOT NULL,
                dialogue_thread_count INTEGER NOT NULL,
                client_tail_thread_count INTEGER NOT NULL,
                response_interval_count INTEGER NOT NULL,
                client_to_manager_interval_count INTEGER NOT NULL,
                manager_to_client_interval_count INTEGER NOT NULL,
                first_response_count INTEGER NOT NULL,
                client_to_manager_median_seconds INTEGER,
                client_to_manager_p90_seconds INTEGER,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS conversation_crm_entity_metrics (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                chat_count INTEGER NOT NULL,
                response_interval_count INTEGER NOT NULL,
                client_to_manager_interval_count INTEGER NOT NULL,
                manager_to_client_interval_count INTEGER NOT NULL,
                first_response_count INTEGER NOT NULL,
                client_to_manager_median_seconds INTEGER,
                client_to_manager_p90_seconds INTEGER,
                PRIMARY KEY (entity_type, entity_id)
            );

            DROP VIEW IF EXISTS conversation_response_event_crm_links;

            CREATE VIEW conversation_response_event_crm_links AS
            SELECT
                interval.chat_id,
                interval.from_turn_index,
                interval.to_turn_index,
                interval.transition_type,
                interval.manager_user_id,
                interval.wait_seconds,
                interval.is_first_manager_response,
                link.entity_type,
                link.entity_id
            FROM conversation_response_intervals AS interval
            JOIN conversation_thread_crm_links AS link
              ON link.chat_id = interval.chat_id;
            """
        )
        await database.commit()


async def build_conversation_aggregates(
    database_path: str,
) -> ConversationAggregatesResult:
    await initialize_conversation_aggregates(database_path)

    async with aiosqlite.connect(database_path) as database:
        database.row_factory = aiosqlite.Row
        await _prepare_connection(database)
        await database.execute("BEGIN IMMEDIATE")

        try:
            await database.execute("DELETE FROM conversation_crm_entity_metrics")
            await database.execute("DELETE FROM conversation_channel_metrics")
            await database.execute("DELETE FROM conversation_manager_metrics")
            await database.execute("DELETE FROM conversation_global_metrics")

            global_cursor = await database.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM conversation_threads) AS thread_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_threads
                        WHERE has_client = 1
                    ) AS client_thread_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_threads
                        WHERE has_dialogue = 1
                    ) AS dialogue_thread_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_response_intervals
                    ) AS response_interval_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_response_intervals
                        WHERE transition_type = 'client_to_manager'
                    ) AS c2m_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_response_intervals
                        WHERE transition_type = 'manager_to_client'
                    ) AS m2c_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_response_intervals
                        WHERE is_first_manager_response = 1
                    ) AS first_response_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_thread_metrics
                        WHERE initial_client_without_manager_response = 1
                    ) AS no_response_count,
                    (
                        SELECT COUNT(*)
                        FROM conversation_thread_metrics
                        WHERE client_tail_after_dialogue = 1
                    ) AS client_tail_count,
                    (
                        SELECT COALESCE(SUM(manager_handoff_count), 0)
                        FROM conversation_thread_metrics
                    ) AS handoff_count
                """
            )
            global_row = await global_cursor.fetchone()
            if global_row is None:
                raise RuntimeError("Conversation global metrics query returned no row")

            await database.execute(
                """
                INSERT INTO conversation_global_metrics (
                    singleton_id,
                    thread_count,
                    client_thread_count,
                    dialogue_thread_count,
                    response_interval_count,
                    client_to_manager_interval_count,
                    manager_to_client_interval_count,
                    first_response_count,
                    initial_client_without_manager_response_count,
                    client_tail_thread_count,
                    manager_handoff_count
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(global_row["thread_count"]),
                    int(global_row["client_thread_count"]),
                    int(global_row["dialogue_thread_count"]),
                    int(global_row["response_interval_count"]),
                    int(global_row["c2m_count"]),
                    int(global_row["m2c_count"]),
                    int(global_row["first_response_count"]),
                    int(global_row["no_response_count"]),
                    int(global_row["client_tail_count"]),
                    int(global_row["handoff_count"]),
                ),
            )

            directory_cursor = await database.execute(
                """
                SELECT entity_id, payload_json
                FROM crm_active_entities
                WHERE entity_type = 'user'
                """
            )
            directory_active = {
                str(row["entity_id"]): _directory_active(row["payload_json"])
                for row in await directory_cursor.fetchall()
            }

            response_cursor = await database.execute(
                """
                SELECT
                    chat_id,
                    manager_user_id,
                    wait_seconds,
                    is_first_manager_response
                FROM conversation_response_intervals
                WHERE transition_type = 'client_to_manager'
                  AND manager_user_id IS NOT NULL
                """
            )
            response_rows = await response_cursor.fetchall()

            manager_waits: dict[str, list[int]] = defaultdict(list)
            manager_first_waits: dict[str, list[int]] = defaultdict(list)
            manager_chats: dict[str, set[str]] = defaultdict(set)

            for row in response_rows:
                manager_id = str(row["manager_user_id"])
                wait_seconds = int(row["wait_seconds"])
                manager_waits[manager_id].append(wait_seconds)
                manager_chats[manager_id].add(str(row["chat_id"]))
                if int(row["is_first_manager_response"]) == 1:
                    manager_first_waits[manager_id].append(wait_seconds)

            manager_rows = []
            for manager_id in sorted(manager_waits, key=lambda value: int(value)):
                waits = manager_waits[manager_id]
                first_waits = manager_first_waits.get(manager_id, [])
                manager_rows.append(
                    (
                        manager_id,
                        directory_active.get(manager_id, 0),
                        len(waits),
                        len(manager_chats[manager_id]),
                        len(first_waits),
                        _median(waits),
                        _p90(waits),
                        _median(first_waits),
                        _p90(first_waits),
                    )
                )

            await database.executemany(
                """
                INSERT INTO conversation_manager_metrics (
                    manager_user_id,
                    directory_active,
                    response_interval_count,
                    response_chat_count,
                    first_response_count,
                    response_median_seconds,
                    response_p90_seconds,
                    first_response_median_seconds,
                    first_response_p90_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                manager_rows,
            )

            channel_thread_cursor = await database.execute(
                """
                SELECT
                    COALESCE(
                        NULLIF(TRIM(connector_title), ''),
                        'UNKNOWN'
                    ) AS channel,
                    COUNT(*) AS thread_count,
                    SUM(has_dialogue) AS dialogue_count,
                    SUM(client_tail_after_dialogue) AS client_tail_count
                FROM conversation_threads
                GROUP BY channel
                """
            )
            channel_thread_rows = {
                str(row["channel"]): row for row in await channel_thread_cursor.fetchall()
            }

            channel_response_cursor = await database.execute(
                """
                SELECT
                    COALESCE(
                        NULLIF(TRIM(thread.connector_title), ''),
                        'UNKNOWN'
                    ) AS channel,
                    interval.transition_type,
                    interval.wait_seconds,
                    interval.is_first_manager_response
                FROM conversation_response_intervals AS interval
                JOIN conversation_threads AS thread
                  ON thread.chat_id = interval.chat_id
                """
            )
            channel_responses = await channel_response_cursor.fetchall()

            channel_intervals: Counter[str] = Counter()
            channel_c2m: Counter[str] = Counter()
            channel_m2c: Counter[str] = Counter()
            channel_first: Counter[str] = Counter()
            channel_c2m_waits: dict[str, list[int]] = defaultdict(list)

            for row in channel_responses:
                channel = str(row["channel"])
                transition = str(row["transition_type"])
                channel_intervals[channel] += 1
                if transition == "client_to_manager":
                    channel_c2m[channel] += 1
                    channel_c2m_waits[channel].append(int(row["wait_seconds"]))
                elif transition == "manager_to_client":
                    channel_m2c[channel] += 1
                if int(row["is_first_manager_response"]) == 1:
                    channel_first[channel] += 1

            channel_rows = []
            for channel in sorted(channel_thread_rows):
                thread_row = channel_thread_rows[channel]
                waits = channel_c2m_waits.get(channel, [])
                channel_rows.append(
                    (
                        channel,
                        int(thread_row["thread_count"]),
                        int(thread_row["dialogue_count"] or 0),
                        int(thread_row["client_tail_count"] or 0),
                        int(channel_intervals[channel]),
                        int(channel_c2m[channel]),
                        int(channel_m2c[channel]),
                        int(channel_first[channel]),
                        _median(waits),
                        _p90(waits),
                    )
                )

            await database.executemany(
                """
                INSERT INTO conversation_channel_metrics (
                    channel,
                    thread_count,
                    dialogue_thread_count,
                    client_tail_thread_count,
                    response_interval_count,
                    client_to_manager_interval_count,
                    manager_to_client_interval_count,
                    first_response_count,
                    client_to_manager_median_seconds,
                    client_to_manager_p90_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                channel_rows,
            )

            crm_cursor = await database.execute(
                """
                SELECT
                    chat_id,
                    from_turn_index,
                    to_turn_index,
                    transition_type,
                    wait_seconds,
                    is_first_manager_response,
                    entity_type,
                    entity_id
                FROM conversation_response_event_crm_links
                """
            )
            crm_rows_raw = await crm_cursor.fetchall()

            # Preserve one factual response event once per exact CRM entity.
            # This intentionally does not create a "global CRM total".
            crm_seen: set[tuple[str, int, int, str, str]] = set()
            crm_chats: dict[tuple[str, str], set[str]] = defaultdict(set)
            crm_intervals: Counter[tuple[str, str]] = Counter()
            crm_c2m: Counter[tuple[str, str]] = Counter()
            crm_m2c: Counter[tuple[str, str]] = Counter()
            crm_first: Counter[tuple[str, str]] = Counter()
            crm_c2m_waits: dict[tuple[str, str], list[int]] = defaultdict(list)

            for row in crm_rows_raw:
                entity_key = (str(row["entity_type"]), str(row["entity_id"]))
                event_key = (
                    str(row["chat_id"]),
                    int(row["from_turn_index"]),
                    int(row["to_turn_index"]),
                    entity_key[0],
                    entity_key[1],
                )
                if event_key in crm_seen:
                    continue
                crm_seen.add(event_key)

                crm_chats[entity_key].add(str(row["chat_id"]))
                crm_intervals[entity_key] += 1

                transition = str(row["transition_type"])
                if transition == "client_to_manager":
                    crm_c2m[entity_key] += 1
                    crm_c2m_waits[entity_key].append(int(row["wait_seconds"]))
                elif transition == "manager_to_client":
                    crm_m2c[entity_key] += 1

                if int(row["is_first_manager_response"]) == 1:
                    crm_first[entity_key] += 1

            crm_rows = []
            for entity_key in sorted(crm_intervals):
                waits = crm_c2m_waits.get(entity_key, [])
                crm_rows.append(
                    (
                        entity_key[0],
                        entity_key[1],
                        len(crm_chats[entity_key]),
                        int(crm_intervals[entity_key]),
                        int(crm_c2m[entity_key]),
                        int(crm_m2c[entity_key]),
                        int(crm_first[entity_key]),
                        _median(waits),
                        _p90(waits),
                    )
                )

            await database.executemany(
                """
                INSERT INTO conversation_crm_entity_metrics (
                    entity_type,
                    entity_id,
                    chat_count,
                    response_interval_count,
                    client_to_manager_interval_count,
                    manager_to_client_interval_count,
                    first_response_count,
                    client_to_manager_median_seconds,
                    client_to_manager_p90_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                crm_rows,
            )

            await database.commit()
        except Exception:
            await database.rollback()
            raise

    return await conversation_aggregates_status(database_path)


async def conversation_aggregates_status(
    database_path: str,
) -> ConversationAggregatesResult:
    async with aiosqlite.connect(database_path) as database:
        await _prepare_connection(database)

        async def scalar(query: str) -> int:
            cursor = await database.execute(query)
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        global_rows = await scalar("SELECT COUNT(*) FROM conversation_global_metrics")
        manager_rows = await scalar("SELECT COUNT(*) FROM conversation_manager_metrics")
        active_manager_rows = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_manager_metrics
            WHERE directory_active = 1
            """
        )
        inactive_manager_rows = await scalar(
            """
            SELECT COUNT(*)
            FROM conversation_manager_metrics
            WHERE directory_active = 0
            """
        )
        channel_rows = await scalar("SELECT COUNT(*) FROM conversation_channel_metrics")
        crm_entity_rows = await scalar("SELECT COUNT(*) FROM conversation_crm_entity_metrics")
        crm_event_link_rows = await scalar(
            "SELECT COUNT(*) FROM conversation_response_event_crm_links"
        )

    return ConversationAggregatesResult(
        global_rows=global_rows,
        manager_rows=manager_rows,
        active_manager_rows=active_manager_rows,
        inactive_manager_rows=inactive_manager_rows,
        channel_rows=channel_rows,
        crm_entity_rows=crm_entity_rows,
        crm_event_link_rows=crm_event_link_rows,
    )
