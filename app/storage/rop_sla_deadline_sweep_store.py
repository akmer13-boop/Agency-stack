from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.services.rop_policy_evaluation import (
    PolicyEvaluation,
    evaluation_to_dict,
)


@dataclass(frozen=True, slots=True)
class SlaDeadlineCandidate:
    source_evaluation_id: int
    policy_profile: str
    rule_key: str
    entity_type: str
    entity_id: int
    stage_id: str
    scheduled_deadline_at: datetime
    attempts: int


@dataclass(frozen=True, slots=True)
class StoredDeadlineSweep:
    source_evaluation_id: int
    status: str
    attempts: int
    result_code: str
    state: str
    verdict: str
    evaluated_as_of: str
    evaluation_json: str
    last_error: str


def _aware_datetime(
    value: Any,
) -> datetime | None:
    if value in (
        None,
        "",
    ):
        return None

    raw = (
        str(value)
        .strip()
        .replace(
            "Z",
            "+00:00",
        )
    )

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(UTC)


def _deadline_from_json(
    value: str,
) -> datetime | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    return _aware_datetime(payload.get("deadline_at"))


async def _prepare(
    database: aiosqlite.Connection,
) -> None:
    await database.execute("PRAGMA busy_timeout=5000")


class RopSlaDeadlineSweepStore:
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
                CREATE TABLE IF NOT EXISTS rop_sla_deadline_sweep (
                    source_evaluation_id INTEGER PRIMARY KEY,
                    policy_profile TEXT NOT NULL,
                    rule_key TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER NOT NULL,
                    stage_id TEXT,
                    scheduled_deadline_at TEXT NOT NULL,
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
                    state TEXT,
                    verdict TEXT,
                    evaluated_as_of TEXT,
                    evaluation_json TEXT,
                    last_error TEXT,
                    processing_started_at TEXT,
                    processed_at TEXT,
                    created_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_rop_sla_deadline_status
                    ON rop_sla_deadline_sweep (
                        status,
                        attempts,
                        scheduled_deadline_at
                    );

                CREATE INDEX IF NOT EXISTS idx_rop_sla_deadline_entity
                    ON rop_sla_deadline_sweep (
                        entity_type,
                        entity_id,
                        rule_key,
                        source_evaluation_id
                    );
                """
            )

            await database.commit()

    async def claim_due(
        self,
        *,
        as_of: datetime,
        max_attempts: int = 3,
    ) -> SlaDeadlineCandidate | None:
        if as_of.tzinfo is None:
            raise ValueError("as_of_timezone_missing")

        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        observed = as_of.astimezone(UTC)

        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            await database.execute("BEGIN IMMEDIATE")

            try:
                cursor = await database.execute(
                    """
                    WITH latest AS (
                        SELECT
                            MAX(id) AS evaluation_id
                        FROM rop_sla_evaluation_log
                        GROUP BY
                            policy_profile,
                            rule_key,
                            entity_type,
                            entity_id
                    )
                    SELECT
                        evaluation.id,
                        evaluation.policy_profile,
                        evaluation.rule_key,
                        evaluation.entity_type,
                        evaluation.entity_id,
                        evaluation.stage_id,
                        evaluation.evaluation_json,
                        sweep.status,
                        sweep.attempts
                    FROM rop_sla_evaluation_log
                        AS evaluation
                    JOIN latest
                      ON latest.evaluation_id =
                         evaluation.id
                    LEFT JOIN rop_sla_deadline_sweep
                        AS sweep
                      ON sweep.source_evaluation_id =
                         evaluation.id
                    WHERE
                        evaluation.policy_profile =
                            'tourism_b2c'
                        AND evaluation.rule_key =
                            'stale_deal'
                        AND evaluation.entity_type =
                            'deal'
                        AND evaluation.state =
                            'ready'
                        AND evaluation.verdict =
                            'open'
                        AND (
                            sweep.source_evaluation_id
                                IS NULL
                            OR (
                                sweep.status = 'failed'
                                AND sweep.attempts < ?
                            )
                        )
                    ORDER BY evaluation.id
                    """,
                    (max_attempts,),
                )

                rows = await cursor.fetchall()

                selected = None

                for row in rows:
                    deadline = _deadline_from_json(str(row[6]))

                    if deadline is None:
                        continue

                    if deadline > observed:
                        continue

                    key = (
                        deadline,
                        int(row[0]),
                    )

                    if selected is None or key < selected[0]:
                        selected = (
                            key,
                            row,
                            deadline,
                        )

                if selected is None:
                    await database.commit()
                    return None

                row = selected[1]
                deadline = selected[2]

                evaluation_id = int(row[0])

                if row[7] is None:
                    await database.execute(
                        """
                        INSERT INTO rop_sla_deadline_sweep (
                            source_evaluation_id,
                            policy_profile,
                            rule_key,
                            entity_type,
                            entity_id,
                            stage_id,
                            scheduled_deadline_at,
                            status,
                            attempts,
                            processing_started_at
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?, ?,
                            'processing',
                            1,
                            CURRENT_TIMESTAMP
                        )
                        """,
                        (
                            evaluation_id,
                            str(row[1]),
                            str(row[2]),
                            str(row[3]),
                            int(row[4]),
                            (str(row[5]) if row[5] is not None else None),
                            deadline.isoformat(),
                        ),
                    )

                    attempts = 1

                else:
                    cursor = await database.execute(
                        """
                        UPDATE rop_sla_deadline_sweep
                        SET
                            status = 'processing',
                            attempts = attempts + 1,
                            result_code = NULL,
                            state = NULL,
                            verdict = NULL,
                            evaluated_as_of = NULL,
                            evaluation_json = NULL,
                            last_error = NULL,
                            processing_started_at =
                                CURRENT_TIMESTAMP,
                            processed_at = NULL
                        WHERE source_evaluation_id = ?
                          AND status = 'failed'
                          AND attempts < ?
                        """,
                        (
                            evaluation_id,
                            max_attempts,
                        ),
                    )

                    if cursor.rowcount != 1:
                        raise RuntimeError("deadline_sweep_claim_conflict")

                    attempts = int(row[8]) + 1

                await database.commit()

                return SlaDeadlineCandidate(
                    source_evaluation_id=(evaluation_id),
                    policy_profile=str(row[1]),
                    rule_key=str(row[2]),
                    entity_type=str(row[3]),
                    entity_id=int(row[4]),
                    stage_id=(str(row[5]) if row[5] is not None else ""),
                    scheduled_deadline_at=deadline,
                    attempts=attempts,
                )

            except Exception:
                await database.rollback()
                raise

    async def complete(
        self,
        candidate: SlaDeadlineCandidate,
        *,
        as_of: datetime,
        evaluation: PolicyEvaluation,
        result_code: str,
    ) -> None:
        if as_of.tzinfo is None:
            raise ValueError("as_of_timezone_missing")

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

            cursor = await database.execute(
                """
                UPDATE rop_sla_deadline_sweep
                SET
                    status = 'completed',
                    result_code = ?,
                    state = ?,
                    verdict = ?,
                    evaluated_as_of = ?,
                    evaluation_json = ?,
                    last_error = NULL,
                    processing_started_at = NULL,
                    processed_at = CURRENT_TIMESTAMP
                WHERE source_evaluation_id = ?
                  AND status = 'processing'
                """,
                (
                    result_code[:120],
                    evaluation.state.value,
                    evaluation.verdict.value,
                    as_of.astimezone(UTC).isoformat(),
                    serialized,
                    candidate.source_evaluation_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("deadline_sweep_complete_conflict")

            await database.commit()

    async def fail(
        self,
        candidate: SlaDeadlineCandidate,
        *,
        error_code: str,
    ) -> None:
        safe_error = (error_code.strip() or "SLA_DEADLINE_SWEEP_ERROR")[:240]

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                UPDATE rop_sla_deadline_sweep
                SET
                    status = 'failed',
                    result_code = ?,
                    last_error = ?,
                    processing_started_at = NULL,
                    processed_at = CURRENT_TIMESTAMP
                WHERE source_evaluation_id = ?
                  AND status = 'processing'
                """,
                (
                    safe_error[:120],
                    safe_error,
                    candidate.source_evaluation_id,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError("deadline_sweep_fail_conflict")

            await database.commit()

    async def get(
        self,
        source_evaluation_id: int,
    ) -> StoredDeadlineSweep | None:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                SELECT
                    source_evaluation_id,
                    status,
                    attempts,
                    result_code,
                    state,
                    verdict,
                    evaluated_as_of,
                    evaluation_json,
                    last_error
                FROM rop_sla_deadline_sweep
                WHERE source_evaluation_id = ?
                LIMIT 1
                """,
                (source_evaluation_id,),
            )

            row = await cursor.fetchone()

        if row is None:
            return None

        return StoredDeadlineSweep(
            source_evaluation_id=int(row[0]),
            status=str(row[1]),
            attempts=int(row[2]),
            result_code=(str(row[3]) if row[3] is not None else ""),
            state=(str(row[4]) if row[4] is not None else ""),
            verdict=(str(row[5]) if row[5] is not None else ""),
            evaluated_as_of=(str(row[6]) if row[6] is not None else ""),
            evaluation_json=(str(row[7]) if row[7] is not None else ""),
            last_error=(str(row[8]) if row[8] is not None else ""),
        )
