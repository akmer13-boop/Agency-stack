from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from app.services.rop_policy_evaluation import (
    PolicyEvaluation,
    evaluation_to_dict,
)


@dataclass(frozen=True, slots=True)
class SlaDispatchEvent:
    inbox_id: int
    event_key: str
    event_name: str
    event_ts: int
    entity_type: str
    entity_id: str
    call_id: str
    attempts: int


@dataclass(frozen=True, slots=True)
class StoredSlaEvaluation:
    evaluation_id: int
    inbox_id: int
    policy_profile: str
    rule_key: str
    state: str
    verdict: str
    entity_type: str
    entity_id: int
    stage_id: str
    evaluated_as_of: str
    evaluation_json: str


@dataclass(frozen=True, slots=True)
class StoredSlaDispatch:
    inbox_id: int
    status: str
    attempts: int
    result_code: str
    targets_observed: int
    evaluations_written: int
    notes_json: str


async def _prepare(
    database: aiosqlite.Connection,
) -> None:
    await database.execute("PRAGMA busy_timeout=5000")


class RopSlaRuntimeStore:
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
                CREATE TABLE IF NOT EXISTS rop_sla_event_dispatch (
                    inbox_id INTEGER PRIMARY KEY,
                    event_key TEXT NOT NULL UNIQUE,
                    event_name TEXT NOT NULL,
                    event_ts INTEGER NOT NULL,
                    status TEXT NOT NULL
                        CHECK (
                            status IN (
                                'processing',
                                'completed',
                                'failed'
                            )
                        ),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_code TEXT,
                    targets_observed INTEGER NOT NULL DEFAULT 0,
                    evaluations_written INTEGER NOT NULL DEFAULT 0,
                    notes_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT,
                    processing_started_at TEXT,
                    processed_at TEXT,
                    created_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_rop_sla_dispatch_status
                    ON rop_sla_event_dispatch (
                        status,
                        attempts,
                        inbox_id
                    );

                CREATE TABLE IF NOT EXISTS rop_sla_evaluation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inbox_id INTEGER NOT NULL,
                    event_key TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    policy_profile TEXT NOT NULL,
                    rule_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER NOT NULL,
                    stage_id TEXT,
                    evaluated_as_of TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        inbox_id,
                        rule_key,
                        entity_type,
                        entity_id
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_rop_sla_eval_entity
                    ON rop_sla_evaluation_log (
                        entity_type,
                        entity_id,
                        rule_key,
                        id
                    );

                CREATE INDEX IF NOT EXISTS idx_rop_sla_eval_verdict
                    ON rop_sla_evaluation_log (
                        state,
                        verdict,
                        id
                    );
                """
            )

            await database.commit()

    async def claim_next(
        self,
        *,
        max_attempts: int = 3,
    ) -> SlaDispatchEvent | None:
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
                        inbox.id,
                        inbox.event_key,
                        inbox.event_name,
                        inbox.event_ts,
                        inbox.entity_type,
                        inbox.entity_id,
                        inbox.call_id,
                        dispatch.status,
                        dispatch.attempts
                    FROM bitrix_event_inbox AS inbox
                    LEFT JOIN rop_sla_event_dispatch
                        AS dispatch
                      ON dispatch.inbox_id = inbox.id
                    WHERE inbox.status = 'completed'
                      AND (
                            dispatch.inbox_id IS NULL
                            OR (
                                dispatch.status = 'failed'
                                AND dispatch.attempts < ?
                            )
                          )
                    ORDER BY inbox.id
                    LIMIT 1
                    """,
                    (max_attempts,),
                )

                row = await cursor.fetchone()

                if row is None:
                    await database.commit()
                    return None

                inbox_id = int(row[0])

                if row[7] is None:
                    await database.execute(
                        """
                        INSERT INTO rop_sla_event_dispatch (
                            inbox_id,
                            event_key,
                            event_name,
                            event_ts,
                            status,
                            attempts,
                            processing_started_at
                        )
                        VALUES (?, ?, ?, ?, 'processing', 1,
                                CURRENT_TIMESTAMP)
                        """,
                        (
                            inbox_id,
                            str(row[1]),
                            str(row[2]),
                            int(row[3]),
                        ),
                    )

                    attempts = 1

                else:
                    await database.execute(
                        """
                        UPDATE rop_sla_event_dispatch
                        SET
                            status = 'processing',
                            attempts = attempts + 1,
                            processing_started_at =
                                CURRENT_TIMESTAMP,
                            processed_at = NULL,
                            result_code = NULL,
                            last_error = NULL
                        WHERE inbox_id = ?
                          AND status = 'failed'
                        """,
                        (inbox_id,),
                    )

                    attempts = int(row[8]) + 1

                await database.commit()

                return SlaDispatchEvent(
                    inbox_id=inbox_id,
                    event_key=str(row[1]),
                    event_name=str(row[2]),
                    event_ts=int(row[3]),
                    entity_type=(str(row[4]) if row[4] is not None else ""),
                    entity_id=(str(row[5]) if row[5] is not None else ""),
                    call_id=(str(row[6]) if row[6] is not None else ""),
                    attempts=attempts,
                )

            except Exception:
                await database.rollback()
                raise

    async def record_evaluation(
        self,
        *,
        event: SlaDispatchEvent,
        policy_profile: str,
        evaluated_as_of: str,
        evaluation: PolicyEvaluation,
    ) -> None:
        await self.initialize()

        payload = evaluation_to_dict(evaluation)

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            await database.execute(
                """
                INSERT INTO rop_sla_evaluation_log (
                    inbox_id,
                    event_key,
                    event_name,
                    policy_profile,
                    rule_key,
                    state,
                    verdict,
                    entity_type,
                    entity_id,
                    stage_id,
                    evaluated_as_of,
                    evaluation_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    inbox_id,
                    rule_key,
                    entity_type,
                    entity_id
                )
                DO UPDATE SET
                    policy_profile =
                        excluded.policy_profile,
                    state = excluded.state,
                    verdict = excluded.verdict,
                    stage_id = excluded.stage_id,
                    evaluated_as_of =
                        excluded.evaluated_as_of,
                    evaluation_json =
                        excluded.evaluation_json,
                    recorded_at =
                        CURRENT_TIMESTAMP
                """,
                (
                    event.inbox_id,
                    event.event_key,
                    event.event_name,
                    policy_profile,
                    evaluation.rule_key,
                    evaluation.state.value,
                    evaluation.verdict.value,
                    evaluation.entity_type,
                    evaluation.entity_id,
                    evaluation.stage_id or None,
                    evaluated_as_of,
                    serialized,
                ),
            )

            await database.commit()

    async def complete(
        self,
        inbox_id: int,
        *,
        result_code: str,
        targets_observed: int,
        evaluations_written: int,
        notes: tuple[str, ...] = (),
    ) -> None:
        await self.initialize()

        notes_json = json.dumps(
            sorted(set(notes)),
            ensure_ascii=False,
            separators=(",", ":"),
        )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                UPDATE rop_sla_event_dispatch
                SET
                    status = 'completed',
                    result_code = ?,
                    targets_observed = ?,
                    evaluations_written = ?,
                    notes_json = ?,
                    last_error = NULL,
                    processing_started_at = NULL,
                    processed_at = CURRENT_TIMESTAMP
                WHERE inbox_id = ?
                  AND status = 'processing'
                """,
                (
                    result_code[:120],
                    targets_observed,
                    evaluations_written,
                    notes_json,
                    inbox_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("sla_dispatch_complete_state_conflict")

            await database.commit()

    async def fail(
        self,
        inbox_id: int,
        *,
        error_code: str,
    ) -> None:
        await self.initialize()

        safe_error = (error_code.strip() or "SLA_DISPATCH_ERROR")[:240]

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                UPDATE rop_sla_event_dispatch
                SET
                    status = 'failed',
                    result_code = ?,
                    last_error = ?,
                    processing_started_at = NULL,
                    processed_at = CURRENT_TIMESTAMP
                WHERE inbox_id = ?
                  AND status = 'processing'
                """,
                (
                    safe_error[:120],
                    safe_error,
                    inbox_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("sla_dispatch_fail_state_conflict")

            await database.commit()

    async def evaluations_for_inbox(
        self,
        inbox_id: int,
    ) -> tuple[StoredSlaEvaluation, ...]:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                SELECT
                    id,
                    inbox_id,
                    policy_profile,
                    rule_key,
                    state,
                    verdict,
                    entity_type,
                    entity_id,
                    stage_id,
                    evaluated_as_of,
                    evaluation_json
                FROM rop_sla_evaluation_log
                WHERE inbox_id = ?
                ORDER BY id
                """,
                (inbox_id,),
            )

            rows = await cursor.fetchall()

        return tuple(
            StoredSlaEvaluation(
                evaluation_id=int(row[0]),
                inbox_id=int(row[1]),
                policy_profile=str(row[2]),
                rule_key=str(row[3]),
                state=str(row[4]),
                verdict=str(row[5]),
                entity_type=str(row[6]),
                entity_id=int(row[7]),
                stage_id=(str(row[8]) if row[8] is not None else ""),
                evaluated_as_of=str(row[9]),
                evaluation_json=str(row[10]),
            )
            for row in rows
        )

    async def dispatch_for_inbox(
        self,
        inbox_id: int,
    ) -> StoredSlaDispatch | None:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                SELECT
                    inbox_id,
                    status,
                    attempts,
                    result_code,
                    targets_observed,
                    evaluations_written,
                    notes_json
                FROM rop_sla_event_dispatch
                WHERE inbox_id = ?
                LIMIT 1
                """,
                (inbox_id,),
            )

            row = await cursor.fetchone()

        if row is None:
            return None

        return StoredSlaDispatch(
            inbox_id=int(row[0]),
            status=str(row[1]),
            attempts=int(row[2]),
            result_code=(str(row[3]) if row[3] is not None else ""),
            targets_observed=int(row[4]),
            evaluations_written=int(row[5]),
            notes_json=str(row[6]),
        )
