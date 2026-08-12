from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

TOMBSTONE_ENTITY_TYPES = frozenset({"deal", "lead", "contact", "company", "activity"})


@dataclass(frozen=True, slots=True)
class CrmTombstone:
    entity_type: str
    entity_id: str
    source_audit_run_id: int
    evidence_kind: str
    evidence_verified_at: str
    tombstoned_at: str | None = None


@dataclass(frozen=True, slots=True)
class CrmSyncRunStatus:
    run_id: int | None
    status: str
    started_at: str | None
    finished_at: str | None
    summary: dict[str, int]
    error_code: str | None = None


async def _prepare_connection(database: aiosqlite.Connection) -> None:
    await database.execute("PRAGMA foreign_keys=ON")
    await database.execute("PRAGMA busy_timeout=5000")


def _stable_json(item: dict[str, Any]) -> str:
    return json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class CrmStore:
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
                CREATE TABLE IF NOT EXISTS crm_raw_entities (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    source_modified_at TEXT,
                    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (entity_type, entity_id)
                );

                CREATE INDEX IF NOT EXISTS idx_crm_raw_entity_type
                    ON crm_raw_entities(entity_type, entity_id);

                CREATE TABLE IF NOT EXISTS crm_entity_tombstones (
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    tombstoned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    source_audit_run_id INTEGER NOT NULL,
                    evidence_kind TEXT NOT NULL,
                    evidence_verified_at TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_id)
                );

                CREATE INDEX IF NOT EXISTS idx_crm_tombstones_type
                    ON crm_entity_tombstones(entity_type, entity_id);

                CREATE VIEW IF NOT EXISTS crm_active_entities AS
                SELECT raw.*
                FROM crm_raw_entities AS raw
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM crm_entity_tombstones AS tombstone
                    WHERE tombstone.entity_type = raw.entity_type
                      AND tombstone.entity_id = raw.entity_id
                );

                CREATE TABLE IF NOT EXISTS crm_sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT
                );
                """
            )
            await database.commit()

    async def start_run(self) -> int:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute("INSERT INTO crm_sync_runs (status) VALUES ('running')")
            await database.commit()
            return int(cursor.lastrowid)

    async def update_run_progress(self, run_id: int, summary: dict[str, int]) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                UPDATE crm_sync_runs
                SET summary_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (json.dumps(summary, sort_keys=True), run_id),
            )
            await database.commit()

    async def finish_run(self, run_id: int, summary: dict[str, int]) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                UPDATE crm_sync_runs
                SET status = 'completed',
                    finished_at = CURRENT_TIMESTAMP,
                    summary_json = ?,
                    error_code = NULL
                WHERE id = ?
                """,
                (json.dumps(summary, sort_keys=True), run_id),
            )
            await database.commit()

    async def fail_run(self, run_id: int, error_code: str) -> None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute(
                """
                UPDATE crm_sync_runs
                SET status = 'failed',
                    finished_at = CURRENT_TIMESTAMP,
                    error_code = ?
                WHERE id = ?
                """,
                (error_code[:120], run_id),
            )
            await database.commit()

    async def upsert_entities(
        self,
        entity_type: str,
        items: list[dict[str, Any]],
        *,
        modified_field: str | None = None,
    ) -> int:
        rows: list[tuple[str, str, str, str, str | None]] = []
        for item in items:
            raw_id = item.get("ID")
            if raw_id is None:
                continue
            payload = _stable_json(item)
            checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            modified = item.get(modified_field) if modified_field else None
            rows.append(
                (
                    entity_type,
                    str(raw_id),
                    payload,
                    checksum,
                    str(modified) if modified is not None else None,
                )
            )

        if not rows:
            return 0

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.executemany(
                """
                INSERT INTO crm_raw_entities (
                    entity_type,
                    entity_id,
                    payload_json,
                    payload_sha256,
                    source_modified_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    payload_sha256 = excluded.payload_sha256,
                    source_modified_at = excluded.source_modified_at,
                    synced_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
            await database.executemany(
                """
                DELETE FROM crm_entity_tombstones
                WHERE entity_type = ? AND entity_id = ?
                """,
                [(entity_type, row[1]) for row in rows],
            )
            await database.commit()
        return len(rows)

    async def apply_tombstones(
        self,
        tombstones: list[CrmTombstone],
    ) -> int:
        if not tombstones:
            return 0

        rows: list[tuple[str, str, int, str, str]] = []
        for tombstone in tombstones:
            if tombstone.entity_type not in TOMBSTONE_ENTITY_TYPES:
                raise ValueError(f"Entity type is not tombstone-enabled: {tombstone.entity_type}")
            if not tombstone.entity_id:
                raise ValueError("Tombstone entity_id must not be empty")
            if tombstone.source_audit_run_id < 1:
                raise ValueError("Tombstone source_audit_run_id must be positive")
            if not tombstone.evidence_kind.strip():
                raise ValueError("Tombstone evidence_kind must not be empty")
            if not tombstone.evidence_verified_at.strip():
                raise ValueError("Tombstone evidence_verified_at must not be empty")
            rows.append(
                (
                    tombstone.entity_type,
                    tombstone.entity_id,
                    tombstone.source_audit_run_id,
                    tombstone.evidence_kind.strip(),
                    tombstone.evidence_verified_at.strip(),
                )
            )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            await database.execute("BEGIN IMMEDIATE")
            try:
                for entity_type, entity_id, *_rest in rows:
                    cursor = await database.execute(
                        """
                        SELECT 1
                        FROM crm_raw_entities
                        WHERE entity_type = ? AND entity_id = ?
                        LIMIT 1
                        """,
                        (entity_type, entity_id),
                    )
                    if await cursor.fetchone() is None:
                        raise ValueError(
                            "Cannot tombstone because raw CRM entity does not exist: "
                            f"{entity_type}:{entity_id}"
                        )

                await database.executemany(
                    """
                    INSERT INTO crm_entity_tombstones (
                        entity_type,
                        entity_id,
                        source_audit_run_id,
                        evidence_kind,
                        evidence_verified_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                        tombstoned_at = CURRENT_TIMESTAMP,
                        source_audit_run_id = excluded.source_audit_run_id,
                        evidence_kind = excluded.evidence_kind,
                        evidence_verified_at = excluded.evidence_verified_at
                    """,
                    rows,
                )
                await database.commit()
            except Exception:
                await database.rollback()
                raise

        return len(rows)

    async def list_tombstones(self) -> list[CrmTombstone]:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT
                    entity_type,
                    entity_id,
                    source_audit_run_id,
                    evidence_kind,
                    evidence_verified_at,
                    tombstoned_at
                FROM crm_entity_tombstones
                ORDER BY entity_type, CAST(entity_id AS INTEGER), entity_id
                """
            )
            rows = await cursor.fetchall()

        return [
            CrmTombstone(
                entity_type=str(row[0]),
                entity_id=str(row[1]),
                source_audit_run_id=int(row[2]),
                evidence_kind=str(row[3]),
                evidence_verified_at=str(row[4]),
                tombstoned_at=str(row[5]) if row[5] is not None else None,
            )
            for row in rows
        ]

    async def count_tombstones(self) -> dict[str, int]:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT entity_type, COUNT(*)
                FROM crm_entity_tombstones
                GROUP BY entity_type
                ORDER BY entity_type
                """
            )
            rows = await cursor.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    async def list_entity_ids(self, entity_type: str) -> set[str]:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT entity_id
                FROM crm_raw_entities
                WHERE entity_type = ?
                """,
                (entity_type,),
            )
            rows = await cursor.fetchall()
        return {str(row[0]) for row in rows}

    async def count_by_type(self) -> dict[str, int]:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT entity_type, COUNT(*)
                FROM crm_raw_entities
                GROUP BY entity_type
                ORDER BY entity_type
                """
            )
            rows = await cursor.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    async def get_last_completed_run_started_at(self) -> str | None:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT started_at
                FROM crm_sync_runs
                WHERE status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    async def get_last_run(self) -> CrmSyncRunStatus:
        async with aiosqlite.connect(self.database_path) as database:
            await _prepare_connection(database)
            cursor = await database.execute(
                """
                SELECT id, status, started_at, finished_at, summary_json, error_code
                FROM crm_sync_runs
                ORDER BY id DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()

        if row is None:
            return CrmSyncRunStatus(
                run_id=None,
                status="never",
                started_at=None,
                finished_at=None,
                summary={},
            )

        try:
            summary = json.loads(row[4] or "{}")
        except (TypeError, json.JSONDecodeError):
            summary = {}
        if not isinstance(summary, dict):
            summary = {}

        return CrmSyncRunStatus(
            run_id=int(row[0]),
            status=str(row[1]),
            started_at=str(row[2]) if row[2] is not None else None,
            finished_at=str(row[3]) if row[3] is not None else None,
            summary={str(key): int(value) for key, value in summary.items()},
            error_code=str(row[5]) if row[5] is not None else None,
        )
