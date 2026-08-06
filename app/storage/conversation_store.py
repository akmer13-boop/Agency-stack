from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from app.domain import UserRole


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    role: str
    content: str
    agent_name: str | None = None


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


class ConversationStore:
    def __init__(self, database_path: str, history_limit: int) -> None:
        self.database_path = database_path
        self.history_limit = history_limit

    async def initialize(self) -> None:
        path = Path(self.database_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute("PRAGMA journal_mode=WAL")
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    agent_name TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (telegram_user_id)
                        REFERENCES telegram_users(telegram_user_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_conversation_user_id
                    ON conversation_messages(telegram_user_id, id);

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await database.commit()

    async def upsert_user(
        self,
        telegram_user_id: int,
        *,
        username: str | None,
        display_name: str | None,
        role: UserRole,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                INSERT INTO telegram_users (
                    telegram_user_id,
                    username,
                    display_name,
                    role
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    username = excluded.username,
                    display_name = excluded.display_name,
                    role = excluded.role,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (telegram_user_id, username, display_name, role.value),
            )
            await database.commit()

    async def add_message(
        self,
        telegram_user_id: int,
        *,
        role: str,
        content: str,
        agent_name: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                INSERT INTO conversation_messages (
                    telegram_user_id,
                    role,
                    content,
                    agent_name
                )
                VALUES (?, ?, ?, ?)
                """,
                (telegram_user_id, role, content, agent_name),
            )
            await database.commit()

    async def get_recent_messages(
        self,
        telegram_user_id: int,
    ) -> list[ConversationMessage]:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT role, content, agent_name
                FROM conversation_messages
                WHERE telegram_user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (telegram_user_id, self.history_limit),
            )
            rows = await cursor.fetchall()

        return [
            ConversationMessage(role=row[0], content=row[1], agent_name=row[2])
            for row in reversed(rows)
        ]

    async def clear_history(self, telegram_user_id: int) -> int:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                "DELETE FROM conversation_messages WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            await database.commit()
            return max(cursor.rowcount, 0)

    async def count_messages(self, telegram_user_id: int) -> int:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT COUNT(*)
                FROM conversation_messages
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def record_event(
        self,
        event_type: str,
        *,
        telegram_user_id: int | None = None,
        details: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                INSERT INTO audit_events (
                    telegram_user_id,
                    event_type,
                    details
                )
                VALUES (?, ?, ?)
                """,
                (telegram_user_id, event_type, details),
            )
            await database.commit()
