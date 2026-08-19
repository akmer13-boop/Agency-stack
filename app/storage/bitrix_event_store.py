from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aiosqlite


@dataclass(frozen=True, slots=True)
class EventEnqueueResult:
    inbox_id: int
    inserted: bool


class BitrixEventInboxStore:
    def __init__(
        self,
        database_path: str,
    ) -> None:
        self.database_path = database_path

    async def initialize(self) -> None:
        path = Path(self.database_path)

        if path.parent != Path("."):
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        async with aiosqlite.connect(self.database_path) as database:
            await database.execute("PRAGMA busy_timeout=5000")

            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS bitrix_event_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    event_name TEXT NOT NULL,
                    event_ts INTEGER NOT NULL,
                    event_handler_id TEXT,
                    entity_type TEXT,
                    entity_id TEXT,
                    call_id TEXT,
                    actor_user_id TEXT,
                    member_id TEXT,
                    domain TEXT,
                    data_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (
                            status IN (
                                'pending',
                                'processing',
                                'completed',
                                'failed'
                            )
                        ),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_bitrix_event_inbox_status
                    ON bitrix_event_inbox(status, id);

                CREATE INDEX IF NOT EXISTS idx_bitrix_event_inbox_entity
                    ON bitrix_event_inbox(
                        entity_type,
                        entity_id,
                        id
                    );

                CREATE INDEX IF NOT EXISTS idx_bitrix_event_inbox_call
                    ON bitrix_event_inbox(
                        call_id,
                        id
                    );
                """
            )

            await database.commit()

    async def enqueue(
        self,
        *,
        event_key: str,
        event_name: str,
        event_ts: int,
        event_handler_id: str,
        entity_type: str,
        entity_id: str,
        call_id: str,
        actor_user_id: str,
        member_id: str,
        domain: str,
        data_json: str,
    ) -> EventEnqueueResult:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await database.execute("PRAGMA busy_timeout=5000")

            before = database.total_changes

            await database.execute(
                """
                INSERT OR IGNORE INTO bitrix_event_inbox (
                    event_key,
                    event_name,
                    event_ts,
                    event_handler_id,
                    entity_type,
                    entity_id,
                    call_id,
                    actor_user_id,
                    member_id,
                    domain,
                    data_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    event_name,
                    event_ts,
                    event_handler_id or None,
                    entity_type or None,
                    entity_id or None,
                    call_id or None,
                    actor_user_id or None,
                    member_id or None,
                    domain or None,
                    data_json,
                ),
            )

            inserted = database.total_changes > before

            cursor = await database.execute(
                """
                SELECT id
                FROM bitrix_event_inbox
                WHERE event_key = ?
                LIMIT 1
                """,
                (event_key,),
            )

            row = await cursor.fetchone()

            if row is None:
                raise RuntimeError("event_inbox_insert_lookup_failed")

            await database.commit()

            return EventEnqueueResult(
                inbox_id=int(row[0]),
                inserted=inserted,
            )
