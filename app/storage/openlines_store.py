from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass(frozen=True, slots=True)
class OpenLinesHistoryWriteResult:
    messages_observed: int
    messages_written: int
    manager_messages: int
    client_messages: int
    system_messages: int
    bot_messages: int
    unknown_messages: int
    text_messages: int
    files_observed: int


@dataclass(frozen=True, slots=True)
class OpenLinesStoreCounts:
    chats: int
    crm_links: int
    sessions: int
    messages: int
    manager_messages: int
    client_messages: int
    system_messages: int
    bot_messages: int
    unknown_messages: int
    backfill_complete_chats: int
    backfill_pending_chats: int


@dataclass(frozen=True, slots=True)
class ChatSyncState:
    chat_id: str
    backfill_complete: bool
    oldest_message_id: int | None
    newest_message_id: int | None
    expected_message_count: int | None
    stored_message_count: int
    pages_loaded: int
    last_error: str | None


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


def _first_text(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().upper()
    if text in {"Y", "YES", "TRUE", "1"}:
        return True
    if text in {"N", "NO", "FALSE", "0"}:
        return False
    return None


def _session_user_metadata(history: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = history.get("users")
    result: dict[str, dict[str, Any]] = {}

    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            user_id = _first_text(value, "id", "ID") or str(key)
            result[user_id] = value
        return result

    if isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict):
                continue
            user_id = _first_text(value, "id", "ID")
            if user_id:
                result[user_id] = value

    return result


def _dialog_user_metadata(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = result.get("users")
    if not isinstance(raw, list):
        return {}

    users: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        user_id = _first_text(item, "id", "ID")
        if user_id:
            users[user_id] = item
    return users


def _looks_like_bot(metadata: dict[str, Any] | None) -> bool:
    if not metadata:
        return False

    if metadata.get("botData"):
        return True

    for key in ("bot", "is_bot", "isBot", "BOT"):
        raw = metadata.get(key)
        if raw is True:
            return True
        if str(raw or "").strip().upper() in {"Y", "YES", "TRUE", "1"}:
            return True

    for key in ("type", "userType", "USER_TYPE"):
        if "BOT" in str(metadata.get(key) or "").strip().upper():
            return True

    return False


def classify_session_sender(
    sender_id: str,
    *,
    directory_user_ids: frozenset[str],
    user_metadata: dict[str, dict[str, Any]],
) -> tuple[str, str | None]:
    if sender_id == "0":
        return "system", None
    if sender_id in directory_user_ids:
        return "manager", sender_id

    metadata = user_metadata.get(sender_id)
    if _looks_like_bot(metadata):
        return "bot", None

    if metadata:
        if _bool(metadata.get("connector")) is True:
            return "client", None
        user_type = str(metadata.get("type") or "").strip().lower()
        external_auth = str(metadata.get("externalAuthId") or "").strip().lower()
        if user_type == "extranet" or external_auth == "imconnector":
            return "client", None

    return "unknown", None


def classify_dialog_sender(
    sender_id: str,
    *,
    directory_user_ids: frozenset[str],
    user_metadata: dict[str, dict[str, Any]],
) -> tuple[str, str | None]:
    if sender_id == "0":
        return "system", None
    if sender_id in directory_user_ids:
        return "manager", sender_id

    metadata = user_metadata.get(sender_id)
    if _looks_like_bot(metadata):
        return "bot", None

    user_type = str((metadata or {}).get("type") or "").strip().lower()
    if user_type in {"extranet", "connector"}:
        return "client", None

    return "unknown", None


def _message_items(history: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    raw = history.get("message")
    result: list[tuple[str, dict[str, Any]]] = []

    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            message_id = _first_text(value, "id", "ID") or str(key)
            result.append((message_id, value))
        return result

    if isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict):
                continue
            message_id = _first_text(value, "id", "ID")
            if message_id:
                result.append((message_id, value))

    return result


def _history_file_count(history: dict[str, Any]) -> int:
    raw = history.get("files")
    if isinstance(raw, (dict, list)):
        return len(raw)
    return 0


def _session_chat(history: dict[str, Any], chat_id: str) -> dict[str, Any]:
    raw = history.get("chat")
    if not isinstance(raw, dict):
        return {}
    direct = raw.get(chat_id)
    if isinstance(direct, dict):
        return direct
    for value in raw.values():
        if isinstance(value, dict) and _first_text(value, "id", "ID") == chat_id:
            return value
    return {}


async def _table_sql(database: aiosqlite.Connection, table: str) -> str:
    cursor = await database.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    row = await cursor.fetchone()
    return str(row[0] or "") if row else ""


async def _migrate_messages_v3(database: aiosqlite.Connection) -> None:
    sql = await _table_sql(database, "openlines_messages")
    if not sql:
        return

    columns_cursor = await database.execute("PRAGMA table_info(openlines_messages)")
    columns = {str(row[1]) for row in await columns_cursor.fetchall()}
    already_v3 = (
        "message_source" in columns and "session_binding_kind" in columns and "'unknown'" in sql
    )
    if already_v3:
        return

    await database.execute("BEGIN IMMEDIATE")
    try:
        await database.execute("DROP TABLE IF EXISTS openlines_messages_v3")
        await database.execute(
            """
            CREATE TABLE openlines_messages_v3 (
                message_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                sender_role TEXT NOT NULL
                    CHECK (
                        sender_role IN (
                            'manager',
                            'client',
                            'system',
                            'bot',
                            'unknown'
                        )
                    ),
                sender_directory_user_id TEXT,
                sent_at TEXT,
                text_content TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                message_source TEXT NOT NULL
                    CHECK (message_source IN ('session_history', 'dialog_history')),
                session_binding_kind TEXT NOT NULL
                    CHECK (session_binding_kind IN ('native', 'chat_history')),
                synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id)
                    REFERENCES openlines_chats(chat_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (session_id)
                    REFERENCES openlines_sessions(session_id)
                    ON DELETE CASCADE
            )
            """
        )
        await database.execute(
            """
            INSERT INTO openlines_messages_v3 (
                message_id,
                chat_id,
                session_id,
                sender_id,
                sender_role,
                sender_directory_user_id,
                sent_at,
                text_content,
                text_sha256,
                message_source,
                session_binding_kind,
                synced_at
            )
            SELECT
                message_id,
                chat_id,
                session_id,
                sender_id,
                sender_role,
                sender_directory_user_id,
                sent_at,
                text_content,
                text_sha256,
                'session_history',
                'native',
                synced_at
            FROM openlines_messages
            """
        )
        await database.execute("DROP TABLE openlines_messages")
        await database.execute("ALTER TABLE openlines_messages_v3 RENAME TO openlines_messages")
        await database.commit()
    except Exception:
        await database.rollback()
        raise


async def _create_message_indexes(database: aiosqlite.Connection) -> None:
    await database.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_openlines_messages_chat_time
            ON openlines_messages(chat_id, sent_at, message_id);

        CREATE INDEX IF NOT EXISTS idx_openlines_messages_role
            ON openlines_messages(sender_role, sender_directory_user_id);

        CREATE INDEX IF NOT EXISTS idx_openlines_messages_chat_numeric_id
            ON openlines_messages(chat_id, CAST(message_id AS INTEGER));
        """
    )


class OpenLinesStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    async def initialize(self) -> None:
        path = Path(self.database_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute("PRAGMA journal_mode=WAL")
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS openlines_chats (
                    chat_id TEXT PRIMARY KEY,
                    connector_id TEXT,
                    connector_title TEXT,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS openlines_crm_links (
                    chat_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, entity_type, entity_id),
                    FOREIGN KEY (chat_id)
                        REFERENCES openlines_chats(chat_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_openlines_crm_entity
                    ON openlines_crm_links(entity_type, entity_id, chat_id);

                CREATE TABLE IF NOT EXISTS openlines_sessions (
                    session_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    messages_observed INTEGER NOT NULL DEFAULT 0,
                    files_observed INTEGER NOT NULL DEFAULT 0,
                    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id)
                        REFERENCES openlines_chats(chat_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_openlines_sessions_chat
                    ON openlines_sessions(chat_id, session_id);

                CREATE TABLE IF NOT EXISTS openlines_messages (
                    message_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_role TEXT NOT NULL
                        CHECK (
                            sender_role IN (
                                'manager',
                                'client',
                                'system',
                                'bot',
                                'unknown'
                            )
                        ),
                    sender_directory_user_id TEXT,
                    sent_at TEXT,
                    text_content TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    message_source TEXT NOT NULL
                        CHECK (
                            message_source IN (
                                'session_history',
                                'dialog_history'
                            )
                        ),
                    session_binding_kind TEXT NOT NULL
                        CHECK (
                            session_binding_kind IN (
                                'native',
                                'chat_history'
                            )
                        ),
                    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id)
                        REFERENCES openlines_chats(chat_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (session_id)
                        REFERENCES openlines_sessions(session_id)
                        ON DELETE CASCADE
                );
                """
            )
            await _migrate_messages_v3(database)
            await _create_message_indexes(database)
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS openlines_chat_sync_state (
                    chat_id TEXT PRIMARY KEY,
                    backfill_complete INTEGER NOT NULL DEFAULT 0,
                    oldest_message_id INTEGER,
                    newest_message_id INTEGER,
                    expected_message_count INTEGER,
                    stored_message_count INTEGER NOT NULL DEFAULT 0,
                    pages_loaded INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    FOREIGN KEY (chat_id)
                        REFERENCES openlines_chats(chat_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_openlines_chat_sync_pending
                    ON openlines_chat_sync_state(
                        backfill_complete,
                        updated_at,
                        chat_id
                    );

                CREATE TABLE IF NOT EXISTS openlines_crm_discovery_state (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    source_activity_max_id INTEGER NOT NULL DEFAULT 0,
                    chats_found INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (entity_type, entity_id)
                );
                """
            )
            await database.commit()

    async def seed_discovery_from_existing_links(self) -> None:
        """One-time migration bridge from B2 links into discovery checkpoints."""
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)

            cursor = await database.execute("SELECT 1 FROM openlines_crm_discovery_state LIMIT 1")
            if await cursor.fetchone() is not None:
                return

            await database.execute(
                """
                WITH activity_owners AS (
                    SELECT
                        CASE CAST(
                            json_extract(payload_json, '$.OWNER_TYPE_ID')
                            AS TEXT
                        )
                            WHEN '1' THEN 'lead'
                            WHEN '2' THEN 'deal'
                            WHEN '3' THEN 'contact'
                            WHEN '4' THEN 'company'
                            ELSE NULL
                        END AS entity_type,
                        CAST(
                            json_extract(payload_json, '$.OWNER_ID')
                            AS TEXT
                        ) AS entity_id,
                        MAX(CAST(entity_id AS INTEGER)) AS max_activity_id
                    FROM crm_active_entities
                    WHERE entity_type = 'activity'
                      AND UPPER(
                            COALESCE(
                                CAST(
                                    json_extract(
                                        payload_json,
                                        '$.PROVIDER_ID'
                                    ) AS TEXT
                                ),
                                ''
                            )
                          ) = 'IMOPENLINES_SESSION'
                      AND json_extract(payload_json, '$.OWNER_ID') IS NOT NULL
                    GROUP BY 1, 2
                ),
                link_counts AS (
                    SELECT
                        entity_type,
                        entity_id,
                        COUNT(*) AS chats_found
                    FROM openlines_crm_links
                    GROUP BY entity_type, entity_id
                )
                INSERT INTO openlines_crm_discovery_state (
                    entity_type,
                    entity_id,
                    source_activity_max_id,
                    chats_found,
                    attempts,
                    last_error
                )
                SELECT
                    links.entity_type,
                    links.entity_id,
                    COALESCE(activity.max_activity_id, 0),
                    links.chats_found,
                    1,
                    NULL
                FROM link_counts AS links
                LEFT JOIN activity_owners AS activity
                  ON activity.entity_type = links.entity_type
                 AND activity.entity_id = links.entity_id
                """
            )
            await database.commit()

    async def discovery_checkpoint(
        self,
        entity_type: str,
        entity_id: str,
    ) -> int:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT source_activity_max_id
                FROM openlines_crm_discovery_state
                WHERE entity_type = ? AND entity_id = ?
                """,
                (entity_type, entity_id),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def mark_discovery(
        self,
        entity_type: str,
        entity_id: str,
        *,
        source_activity_max_id: int,
        chats_found: int,
        error_code: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                INSERT INTO openlines_crm_discovery_state (
                    entity_type,
                    entity_id,
                    source_activity_max_id,
                    chats_found,
                    attempts,
                    last_error
                )
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    source_activity_max_id = CASE
                        WHEN excluded.last_error IS NULL
                        THEN excluded.source_activity_max_id
                        ELSE openlines_crm_discovery_state.source_activity_max_id
                    END,
                    chats_found = CASE
                        WHEN excluded.last_error IS NULL
                        THEN excluded.chats_found
                        ELSE openlines_crm_discovery_state.chats_found
                    END,
                    attempts = openlines_crm_discovery_state.attempts + 1,
                    last_error = excluded.last_error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    entity_type,
                    entity_id,
                    source_activity_max_id,
                    chats_found,
                    error_code,
                ),
            )
            await database.commit()

    async def upsert_chat_link(
        self,
        chat: dict[str, Any],
        *,
        entity_type: str,
        entity_id: str,
    ) -> str:
        raw_chat_id = _first_text(chat, "CHAT_ID", "chatId", "ID", "id")
        if raw_chat_id is None or not raw_chat_id.isdigit():
            raise ValueError("Open Lines chat has no numeric chat ID")

        chat_id = raw_chat_id
        connector_id = _first_text(chat, "CONNECTOR_ID", "connectorId")
        connector_title = _first_text(
            chat,
            "CONNECTOR_TITLE",
            "connectorTitle",
            "LINE_NAME",
            "lineName",
        )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                INSERT INTO openlines_chats (
                    chat_id,
                    connector_id,
                    connector_title
                )
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    connector_id = COALESCE(
                        excluded.connector_id,
                        openlines_chats.connector_id
                    ),
                    connector_title = COALESCE(
                        excluded.connector_title,
                        openlines_chats.connector_title
                    ),
                    synced_at = CURRENT_TIMESTAMP
                """,
                (chat_id, connector_id, connector_title),
            )
            await database.execute(
                """
                INSERT OR IGNORE INTO openlines_crm_links (
                    chat_id,
                    entity_type,
                    entity_id
                )
                VALUES (?, ?, ?)
                """,
                (chat_id, entity_type, entity_id),
            )
            await database.execute(
                """
                INSERT OR IGNORE INTO openlines_chat_sync_state (chat_id)
                VALUES (?)
                """,
                (chat_id,),
            )
            await database.commit()

        return chat_id

    async def _ensure_history_session(
        self,
        database: aiosqlite.Connection,
        chat_id: str,
        *,
        messages_observed: int,
    ) -> str:
        session_id = f"chat:{chat_id}:all-history"
        await database.execute(
            """
            INSERT INTO openlines_sessions (
                session_id,
                chat_id,
                messages_observed,
                files_observed
            )
            VALUES (?, ?, ?, 0)
            ON CONFLICT(session_id) DO UPDATE SET
                messages_observed = MAX(
                    openlines_sessions.messages_observed,
                    excluded.messages_observed
                ),
                synced_at = CURRENT_TIMESTAMP
            """,
            (session_id, chat_id, messages_observed),
        )
        return session_id

    async def upsert_history(
        self,
        chat_id: str,
        history: dict[str, Any],
        *,
        directory_user_ids: frozenset[str],
    ) -> OpenLinesHistoryWriteResult:
        raw_session_id = _first_text(
            history,
            "sessionId",
            "SESSION_ID",
            "session_id",
        )
        session_id = raw_session_id or f"chat:{chat_id}:current"

        items = _message_items(history)
        metadata = _session_user_metadata(history)
        files_observed = _history_file_count(history)

        role_counts = {
            "manager": 0,
            "client": 0,
            "system": 0,
            "bot": 0,
            "unknown": 0,
        }
        rows: list[tuple[str, str, str, str, str, str | None, str | None, str, str]] = []

        for message_id, item in items:
            sender_id = _first_text(item, "senderid", "senderId", "SENDER_ID") or "0"
            role, directory_user_id = classify_session_sender(
                sender_id,
                directory_user_ids=directory_user_ids,
                user_metadata=metadata,
            )
            role_counts[role] += 1

            text = _first_text(item, "text", "TEXT", "message", "MESSAGE") or ""
            sent_at = _first_text(
                item,
                "date",
                "DATE",
                "dateCreate",
                "DATE_CREATE",
                "createdAt",
            )
            checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
            rows.append(
                (
                    message_id,
                    chat_id,
                    session_id,
                    sender_id,
                    role,
                    directory_user_id,
                    sent_at,
                    text,
                    checksum,
                )
            )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute("BEGIN IMMEDIATE")
            try:
                await database.execute(
                    """
                    INSERT INTO openlines_sessions (
                        session_id,
                        chat_id,
                        messages_observed,
                        files_observed
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        chat_id = excluded.chat_id,
                        messages_observed = excluded.messages_observed,
                        files_observed = excluded.files_observed,
                        synced_at = CURRENT_TIMESTAMP
                    """,
                    (session_id, chat_id, len(items), files_observed),
                )

                if rows:
                    await database.executemany(
                        """
                        INSERT INTO openlines_messages (
                            message_id,
                            chat_id,
                            session_id,
                            sender_id,
                            sender_role,
                            sender_directory_user_id,
                            sent_at,
                            text_content,
                            text_sha256,
                            message_source,
                            session_binding_kind
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'session_history', 'native')
                        ON CONFLICT(message_id) DO UPDATE SET
                            chat_id = excluded.chat_id,
                            session_id = excluded.session_id,
                            sender_id = excluded.sender_id,
                            sender_role = excluded.sender_role,
                            sender_directory_user_id = excluded.sender_directory_user_id,
                            sent_at = excluded.sent_at,
                            text_content = excluded.text_content,
                            text_sha256 = excluded.text_sha256,
                            message_source = 'session_history',
                            session_binding_kind = 'native',
                            synced_at = CURRENT_TIMESTAMP
                        """,
                        rows,
                    )

                await database.commit()
            except Exception:
                await database.rollback()
                raise

        return OpenLinesHistoryWriteResult(
            messages_observed=len(items),
            messages_written=len(rows),
            manager_messages=role_counts["manager"],
            client_messages=role_counts["client"],
            system_messages=role_counts["system"],
            bot_messages=role_counts["bot"],
            unknown_messages=role_counts["unknown"],
            text_messages=sum(bool(row[7].strip()) for row in rows),
            files_observed=files_observed,
        )

    async def upsert_dialog_page(
        self,
        chat_id: str,
        result: dict[str, Any],
        *,
        directory_user_ids: frozenset[str],
        expected_message_count: int | None,
    ) -> OpenLinesHistoryWriteResult:
        raw_messages = result.get("messages")
        messages = (
            [item for item in raw_messages if isinstance(item, dict)]
            if isinstance(raw_messages, list)
            else []
        )
        metadata = _dialog_user_metadata(result)
        raw_files = result.get("files")
        files_observed = len(raw_files) if isinstance(raw_files, list) else 0

        role_counts = {
            "manager": 0,
            "client": 0,
            "system": 0,
            "bot": 0,
            "unknown": 0,
        }

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute("BEGIN IMMEDIATE")
            try:
                session_id = await self._ensure_history_session(
                    database,
                    chat_id,
                    messages_observed=expected_message_count or len(messages),
                )

                rows: list[
                    tuple[
                        str,
                        str,
                        str,
                        str,
                        str,
                        str | None,
                        str | None,
                        str,
                        str,
                    ]
                ] = []

                for item in messages:
                    message_id = _first_text(item, "id", "ID")
                    if not message_id or not message_id.isdigit():
                        continue

                    sender_id = (
                        _first_text(
                            item,
                            "author_id",
                            "authorId",
                            "AUTHOR_ID",
                        )
                        or "0"
                    )
                    role, directory_user_id = classify_dialog_sender(
                        sender_id,
                        directory_user_ids=directory_user_ids,
                        user_metadata=metadata,
                    )
                    role_counts[role] += 1

                    text = _first_text(item, "text", "TEXT") or ""
                    sent_at = _first_text(item, "date", "DATE")
                    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()

                    rows.append(
                        (
                            message_id,
                            chat_id,
                            session_id,
                            sender_id,
                            role,
                            directory_user_id,
                            sent_at,
                            text,
                            checksum,
                        )
                    )

                if rows:
                    await database.executemany(
                        """
                        INSERT INTO openlines_messages (
                            message_id,
                            chat_id,
                            session_id,
                            sender_id,
                            sender_role,
                            sender_directory_user_id,
                            sent_at,
                            text_content,
                            text_sha256,
                            message_source,
                            session_binding_kind
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'dialog_history', 'chat_history')
                        ON CONFLICT(message_id) DO UPDATE SET
                            chat_id = excluded.chat_id,
                            sender_id = excluded.sender_id,
                            sender_role = CASE
                                WHEN openlines_messages.sender_role = 'unknown'
                                THEN excluded.sender_role
                                ELSE openlines_messages.sender_role
                            END,
                            sender_directory_user_id = COALESCE(
                                openlines_messages.sender_directory_user_id,
                                excluded.sender_directory_user_id
                            ),
                            sent_at = COALESCE(excluded.sent_at, openlines_messages.sent_at),
                            text_content = excluded.text_content,
                            text_sha256 = excluded.text_sha256,
                            synced_at = CURRENT_TIMESTAMP
                        """,
                        rows,
                    )

                await database.commit()
            except Exception:
                await database.rollback()
                raise

        return OpenLinesHistoryWriteResult(
            messages_observed=len(messages),
            messages_written=len(rows),
            manager_messages=role_counts["manager"],
            client_messages=role_counts["client"],
            system_messages=role_counts["system"],
            bot_messages=role_counts["bot"],
            unknown_messages=role_counts["unknown"],
            text_messages=sum(bool(row[7].strip()) for row in rows),
            files_observed=files_observed,
        )

    async def expected_message_count_from_history(
        self,
        chat_id: str,
        history: dict[str, Any],
    ) -> int | None:
        chat = _session_chat(history, chat_id)
        raw = chat.get("messageCount")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    async def chat_message_bounds(
        self,
        chat_id: str,
    ) -> tuple[int | None, int | None, int]:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT
                    MIN(CAST(message_id AS INTEGER)),
                    MAX(CAST(message_id AS INTEGER)),
                    COUNT(*)
                FROM openlines_messages
                WHERE chat_id = ?
                  AND message_id GLOB '[0-9]*'
                """,
                (chat_id,),
            )
            row = await cursor.fetchone()

        if not row:
            return None, None, 0
        oldest = int(row[0]) if row[0] is not None else None
        newest = int(row[1]) if row[1] is not None else None
        return oldest, newest, int(row[2])

    async def get_chat_sync_state(self, chat_id: str) -> ChatSyncState:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT
                    backfill_complete,
                    oldest_message_id,
                    newest_message_id,
                    expected_message_count,
                    stored_message_count,
                    pages_loaded,
                    last_error
                FROM openlines_chat_sync_state
                WHERE chat_id = ?
                """,
                (chat_id,),
            )
            row = await cursor.fetchone()

        if not row:
            return ChatSyncState(
                chat_id=chat_id,
                backfill_complete=False,
                oldest_message_id=None,
                newest_message_id=None,
                expected_message_count=None,
                stored_message_count=0,
                pages_loaded=0,
                last_error=None,
            )

        return ChatSyncState(
            chat_id=chat_id,
            backfill_complete=bool(row[0]),
            oldest_message_id=int(row[1]) if row[1] is not None else None,
            newest_message_id=int(row[2]) if row[2] is not None else None,
            expected_message_count=int(row[3]) if row[3] is not None else None,
            stored_message_count=int(row[4]),
            pages_loaded=int(row[5]),
            last_error=str(row[6]) if row[6] is not None else None,
        )

    async def update_chat_sync_state(
        self,
        chat_id: str,
        *,
        expected_message_count: int | None,
        pages_added: int = 0,
        backfill_complete: bool | None = None,
        error_code: str | None = None,
    ) -> ChatSyncState:
        oldest, newest, stored = await self.chat_message_bounds(chat_id)
        current = await self.get_chat_sync_state(chat_id)
        complete = current.backfill_complete if backfill_complete is None else backfill_complete
        expected = (
            expected_message_count
            if expected_message_count is not None
            else current.expected_message_count
        )

        if expected is not None and stored >= expected:
            complete = True

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                INSERT INTO openlines_chat_sync_state (
                    chat_id,
                    backfill_complete,
                    oldest_message_id,
                    newest_message_id,
                    expected_message_count,
                    stored_message_count,
                    pages_loaded,
                    last_error,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP END)
                ON CONFLICT(chat_id) DO UPDATE SET
                    backfill_complete = excluded.backfill_complete,
                    oldest_message_id = excluded.oldest_message_id,
                    newest_message_id = excluded.newest_message_id,
                    expected_message_count = excluded.expected_message_count,
                    stored_message_count = excluded.stored_message_count,
                    pages_loaded = openlines_chat_sync_state.pages_loaded + ?,
                    last_error = excluded.last_error,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CASE
                        WHEN excluded.backfill_complete = 1
                        THEN COALESCE(
                            openlines_chat_sync_state.completed_at,
                            CURRENT_TIMESTAMP
                        )
                        ELSE NULL
                    END
                """,
                (
                    chat_id,
                    1 if complete else 0,
                    oldest,
                    newest,
                    expected,
                    stored,
                    current.pages_loaded + pages_added,
                    error_code,
                    1 if complete else 0,
                    pages_added,
                ),
            )
            await database.commit()

        return await self.get_chat_sync_state(chat_id)

    async def list_chat_ids_for_sync(self, *, limit: int) -> list[str]:
        if limit < 1:
            return []
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT chat.chat_id
                FROM openlines_chats AS chat
                LEFT JOIN openlines_chat_sync_state AS state
                  ON state.chat_id = chat.chat_id
                ORDER BY
                    COALESCE(state.backfill_complete, 0) ASC,
                    COALESCE(state.updated_at, '1970-01-01') ASC,
                    CAST(chat.chat_id AS INTEGER) DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [str(row[0]) for row in rows]

    async def counts(self) -> OpenLinesStoreCounts:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)

            async def scalar(query: str, params: tuple[Any, ...] = ()) -> int:
                cursor = await database.execute(query, params)
                row = await cursor.fetchone()
                return int(row[0]) if row else 0

            chats = await scalar("SELECT COUNT(*) FROM openlines_chats")
            crm_links = await scalar("SELECT COUNT(*) FROM openlines_crm_links")
            sessions = await scalar("SELECT COUNT(*) FROM openlines_sessions")
            messages = await scalar("SELECT COUNT(*) FROM openlines_messages")
            manager_messages = await scalar(
                "SELECT COUNT(*) FROM openlines_messages WHERE sender_role = 'manager'"
            )
            client_messages = await scalar(
                "SELECT COUNT(*) FROM openlines_messages WHERE sender_role = 'client'"
            )
            system_messages = await scalar(
                "SELECT COUNT(*) FROM openlines_messages WHERE sender_role = 'system'"
            )
            bot_messages = await scalar(
                "SELECT COUNT(*) FROM openlines_messages WHERE sender_role = 'bot'"
            )
            unknown_messages = await scalar(
                "SELECT COUNT(*) FROM openlines_messages WHERE sender_role = 'unknown'"
            )
            backfill_complete_chats = await scalar(
                """
                SELECT COUNT(*)
                FROM openlines_chat_sync_state
                WHERE backfill_complete = 1
                """
            )
            backfill_pending_chats = await scalar(
                """
                SELECT COUNT(*)
                FROM openlines_chats AS chat
                LEFT JOIN openlines_chat_sync_state AS state
                  ON state.chat_id = chat.chat_id
                WHERE COALESCE(state.backfill_complete, 0) = 0
                """
            )

        return OpenLinesStoreCounts(
            chats=chats,
            crm_links=crm_links,
            sessions=sessions,
            messages=messages,
            manager_messages=manager_messages,
            client_messages=client_messages,
            system_messages=system_messages,
            bot_messages=bot_messages,
            unknown_messages=unknown_messages,
            backfill_complete_chats=backfill_complete_chats,
            backfill_pending_chats=backfill_pending_chats,
        )
