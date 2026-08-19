from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from app.integrations.bitrix24.event_privacy import (
    minimize_bitrix_event_data_json,
)


@dataclass(frozen=True, slots=True)
class EventEnqueueResult:
    inbox_id: int
    inserted: bool


@dataclass(frozen=True, slots=True)
class BitrixInboxEvent:
    inbox_id: int
    event_key: str
    event_name: str
    event_ts: int
    event_handler_id: str
    entity_type: str
    entity_id: str
    call_id: str
    actor_user_id: str
    member_id: str
    domain: str
    data_json: str
    status: str
    attempts: int
    last_error: str


@dataclass(frozen=True, slots=True)
class InboxStatusCounts:
    pending: int
    processing: int
    completed: int
    failed: int


async def _prepare(
    database: aiosqlite.Connection,
) -> None:
    await database.execute("PRAGMA busy_timeout=5000")


async def _columns(
    database: aiosqlite.Connection,
    table: str,
) -> set[str]:
    cursor = await database.execute(f"PRAGMA table_info({table})")

    rows = await cursor.fetchall()

    return {str(row[1]) for row in rows}


class BitrixEventInboxStore:
    def __init__(
        self,
        database_path: str,
    ) -> None:
        self.database_path = database_path

    async def initialize(
        self,
    ) -> None:
        path = Path(self.database_path)

        if path.parent != Path("."):
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

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
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processing_started_at TEXT,
                    processed_at TEXT,
                    result_code TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_bitrix_event_inbox_status
                    ON bitrix_event_inbox(
                        status,
                        attempts,
                        id
                    );

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

                CREATE TABLE IF NOT EXISTS bitrix_call_evidence (
                    event_key TEXT PRIMARY KEY,
                    inbox_id INTEGER NOT NULL,
                    call_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    event_ts INTEGER NOT NULL,
                    actor_user_id TEXT,
                    call_failed_code TEXT,
                    call_duration_seconds INTEGER,
                    crm_activity_id TEXT,
                    crm_entity_type TEXT,
                    crm_entity_id TEXT,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_bitrix_call_evidence_call
                    ON bitrix_call_evidence(
                        call_id,
                        event_ts
                    );

                CREATE INDEX IF NOT EXISTS idx_bitrix_call_evidence_crm
                    ON bitrix_call_evidence(
                        crm_entity_type,
                        crm_entity_id,
                        event_ts
                    );

                CREATE TABLE IF NOT EXISTS bitrix_entity_delete_observations (
                    event_key TEXT PRIMARY KEY,
                    inbox_id INTEGER NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    event_ts INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_bitrix_delete_observation_entity
                    ON bitrix_entity_delete_observations(
                        entity_type,
                        entity_id,
                        event_ts
                    );
                """
            )

            existing = await _columns(
                database,
                "bitrix_event_inbox",
            )

            migrations = {
                "processing_started_at": (
                    "ALTER TABLE bitrix_event_inbox ADD COLUMN processing_started_at TEXT"
                ),
                "processed_at": ("ALTER TABLE bitrix_event_inbox ADD COLUMN processed_at TEXT"),
                "result_code": ("ALTER TABLE bitrix_event_inbox ADD COLUMN result_code TEXT"),
            }

            for (
                column,
                statement,
            ) in migrations.items():
                if column not in existing:
                    await database.execute(statement)

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

        safe_data_json = minimize_bitrix_event_data_json(
            entity_type,
            data_json,
        )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

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
                    safe_data_json,
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

    @staticmethod
    def _event_from_row(
        row: tuple,
    ) -> BitrixInboxEvent:
        return BitrixInboxEvent(
            inbox_id=int(row[0]),
            event_key=str(row[1]),
            event_name=str(row[2]),
            event_ts=int(row[3]),
            event_handler_id=(str(row[4]) if row[4] is not None else ""),
            entity_type=(str(row[5]) if row[5] is not None else ""),
            entity_id=(str(row[6]) if row[6] is not None else ""),
            call_id=(str(row[7]) if row[7] is not None else ""),
            actor_user_id=(str(row[8]) if row[8] is not None else ""),
            member_id=(str(row[9]) if row[9] is not None else ""),
            domain=(str(row[10]) if row[10] is not None else ""),
            data_json=str(row[11]),
            status=str(row[12]),
            attempts=int(row[13]),
            last_error=(str(row[14]) if row[14] is not None else ""),
        )

    async def claim_next(
        self,
        *,
        max_attempts: int = 3,
    ) -> BitrixInboxEvent | None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            await database.execute("BEGIN IMMEDIATE")

            try:
                cursor = await database.execute(
                    """
                    SELECT
                        id,
                        status
                    FROM bitrix_event_inbox
                    WHERE (
                        status = 'pending'
                        OR (
                            status = 'failed'
                            AND attempts < ?
                        )
                    )
                    ORDER BY id
                    LIMIT 1
                    """,
                    (max_attempts,),
                )

                candidate = await cursor.fetchone()

                if candidate is None:
                    await database.commit()
                    return None

                inbox_id = int(candidate[0])

                await database.execute(
                    """
                    UPDATE bitrix_event_inbox
                    SET
                        status = 'processing',
                        attempts = attempts + 1,
                        processing_started_at =
                            CURRENT_TIMESTAMP,
                        processed_at = NULL,
                        last_error = NULL
                    WHERE id = ?
                    """,
                    (inbox_id,),
                )

                cursor = await database.execute(
                    """
                    SELECT
                        id,
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
                        data_json,
                        status,
                        attempts,
                        last_error
                    FROM bitrix_event_inbox
                    WHERE id = ?
                    LIMIT 1
                    """,
                    (inbox_id,),
                )

                row = await cursor.fetchone()

                if row is None:
                    raise RuntimeError("event_claim_lookup_failed")

                await database.commit()

                return self._event_from_row(row)

            except Exception:
                await database.rollback()
                raise

    async def complete(
        self,
        inbox_id: int,
        *,
        result_code: str,
    ) -> None:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                UPDATE bitrix_event_inbox
                SET
                    status = 'completed',
                    result_code = ?,
                    last_error = NULL,
                    processing_started_at = NULL,
                    processed_at = CURRENT_TIMESTAMP
                WHERE
                    id = ?
                    AND status = 'processing'
                """,
                (
                    result_code[:120],
                    inbox_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("event_complete_state_conflict")

            await database.commit()

    async def fail(
        self,
        inbox_id: int,
        *,
        error_code: str,
    ) -> None:
        await self.initialize()

        safe_error = (error_code.strip() or "EVENT_PROCESSING_ERROR")[:240]

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                UPDATE bitrix_event_inbox
                SET
                    status = 'failed',
                    result_code = ?,
                    last_error = ?,
                    processing_started_at = NULL,
                    processed_at = CURRENT_TIMESTAMP
                WHERE
                    id = ?
                    AND status = 'processing'
                """,
                (
                    safe_error[:120],
                    safe_error,
                    inbox_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("event_fail_state_conflict")

            await database.commit()

    async def record_call_evidence(
        self,
        event: BitrixInboxEvent,
        *,
        call_failed_code: str = "",
        call_duration_seconds: int | None = None,
        crm_activity_id: str = "",
        crm_entity_type: str = "",
        crm_entity_id: str = "",
    ) -> None:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            await database.execute(
                """
                INSERT OR IGNORE INTO bitrix_call_evidence (
                    event_key,
                    inbox_id,
                    call_id,
                    event_name,
                    event_ts,
                    actor_user_id,
                    call_failed_code,
                    call_duration_seconds,
                    crm_activity_id,
                    crm_entity_type,
                    crm_entity_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_key,
                    event.inbox_id,
                    event.call_id,
                    event.event_name,
                    event.event_ts,
                    event.actor_user_id or None,
                    call_failed_code or None,
                    call_duration_seconds,
                    crm_activity_id or None,
                    crm_entity_type or None,
                    crm_entity_id or None,
                ),
            )

            await database.commit()

    async def record_delete_observation(
        self,
        event: BitrixInboxEvent,
    ) -> None:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            await database.execute(
                """
                INSERT OR IGNORE INTO bitrix_entity_delete_observations (
                    event_key,
                    inbox_id,
                    entity_type,
                    entity_id,
                    event_name,
                    event_ts
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_key,
                    event.inbox_id,
                    event.entity_type,
                    event.entity_id,
                    event.event_name,
                    event.event_ts,
                ),
            )

            await database.commit()

    async def count_by_status(
        self,
    ) -> InboxStatusCounts:
        await self.initialize()

        counts = {
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
        }

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                SELECT
                    status,
                    COUNT(*)
                FROM bitrix_event_inbox
                GROUP BY status
                """
            )

            rows = await cursor.fetchall()

        for (
            status,
            count,
        ) in rows:
            key = str(status)

            if key in counts:
                counts[key] = int(count)

        return InboxStatusCounts(
            pending=counts["pending"],
            processing=counts["processing"],
            completed=counts["completed"],
            failed=counts["failed"],
        )
