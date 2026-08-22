from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite


@dataclass(frozen=True, slots=True)
class VoximplantStatisticFact:
    statistic_id: str
    call_id: str
    call_start_at: str
    call_failed_code: str
    call_duration_seconds: int | None
    crm_activity_id: str
    crm_entity_type: str
    crm_entity_id: str
    portal_user_id: str
    call_type: str


@dataclass(frozen=True, slots=True)
class VoximplantCoverage:
    window_start: datetime
    window_end: datetime
    last_run_id: int


_COVERAGE_KEY = "voximplant_statistics"


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _prepare(
    database: aiosqlite.Connection,
) -> None:
    await database.execute(
        "PRAGMA busy_timeout=5000"
    )


class RopVoximplantReconciliationStore:
    def __init__(
        self,
        database_path: str,
    ) -> None:
        self.database_path = database_path

    async def initialize(
        self,
    ) -> None:
        path = Path(
            self.database_path
        )

        if path.parent != Path("."):
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        async with aiosqlite.connect(
            self.database_path
        ) as database:
            await _prepare(database)

            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS
                    rop_voximplant_statistic_facts (
                        statistic_id TEXT PRIMARY KEY,
                        call_id TEXT NOT NULL,
                        call_start_at TEXT NOT NULL,
                        call_failed_code TEXT,
                        call_duration_seconds INTEGER,
                        crm_activity_id TEXT,
                        crm_entity_type TEXT,
                        crm_entity_id TEXT,
                        portal_user_id TEXT,
                        call_type TEXT,
                        last_seen_run_id INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                            DEFAULT CURRENT_TIMESTAMP
                    );

                CREATE INDEX IF NOT EXISTS
                    idx_rop_voximplant_fact_call
                ON rop_voximplant_statistic_facts(
                    call_id
                );

                CREATE INDEX IF NOT EXISTS
                    idx_rop_voximplant_fact_start
                ON rop_voximplant_statistic_facts(
                    call_start_at
                );

                CREATE TABLE IF NOT EXISTS
                    rop_voximplant_reconciliation_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        window_start TEXT NOT NULL,
                        window_end TEXT NOT NULL,
                        api_total INTEGER NOT NULL,
                        fetched_rows INTEGER NOT NULL,
                        unique_statistic_ids INTEGER NOT NULL,
                        unique_call_ids INTEGER NOT NULL,
                        successful_calls INTEGER NOT NULL,
                        successful_with_duration INTEGER NOT NULL,
                        policy_candidate_calls INTEGER NOT NULL
                            DEFAULT 0,
                        crm_linked_calls INTEGER NOT NULL,
                        end_event_matches INTEGER NOT NULL,
                        missing_end_events INTEGER NOT NULL,
                        successful_start_matches INTEGER NOT NULL,
                        successful_missing_start_events
                            INTEGER NOT NULL,
                        orphan_start_events INTEGER NOT NULL,
                        orphan_end_events INTEGER NOT NULL,
                        pagination_complete INTEGER NOT NULL
                            CHECK (
                                pagination_complete IN (0, 1)
                            ),
                        realtime_complete INTEGER NOT NULL
                            CHECK (
                                realtime_complete IN (0, 1)
                            ),
                        created_at TEXT NOT NULL
                            DEFAULT CURRENT_TIMESTAMP
                    );

                CREATE TABLE IF NOT EXISTS
                    rop_voximplant_coverage (
                        source_key TEXT PRIMARY KEY,
                        window_start TEXT NOT NULL,
                        window_end TEXT NOT NULL,
                        last_run_id INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                            DEFAULT CURRENT_TIMESTAMP
                    );
                """
            )

            cursor = await database.execute(
                """
                PRAGMA table_info(
                    rop_voximplant_statistic_facts
                )
                """
            )

            columns = {
                str(row[1])
                for row in await cursor.fetchall()
            }

            if "crm_entity_type" not in columns:
                await database.execute(
                    """
                    ALTER TABLE
                        rop_voximplant_statistic_facts
                    ADD COLUMN
                        crm_entity_type TEXT
                    """
                )

            if "crm_entity_id" not in columns:
                await database.execute(
                    """
                    ALTER TABLE
                        rop_voximplant_statistic_facts
                    ADD COLUMN
                        crm_entity_id TEXT
                    """
                )

            if "portal_user_id" not in columns:
                await database.execute(
                    """
                    ALTER TABLE
                        rop_voximplant_statistic_facts
                    ADD COLUMN
                        portal_user_id TEXT
                    """
                )

            if "call_type" not in columns:
                await database.execute(
                    """
                    ALTER TABLE
                        rop_voximplant_statistic_facts
                    ADD COLUMN
                        call_type TEXT
                    """
                )

            cursor = await database.execute(
                """
                PRAGMA table_info(
                    rop_voximplant_reconciliation_runs
                )
                """
            )
            run_columns = {
                str(row[1])
                for row in await cursor.fetchall()
            }

            if "policy_candidate_calls" not in run_columns:
                await database.execute(
                    """
                    ALTER TABLE
                        rop_voximplant_reconciliation_runs
                    ADD COLUMN
                        policy_candidate_calls INTEGER NOT NULL
                        DEFAULT 0
                    """
                )

            await database.commit()

    async def get_coverage(self) -> VoximplantCoverage | None:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)
            cursor = await database.execute(
                """
                SELECT
                    window_start,
                    window_end,
                    last_run_id
                FROM rop_voximplant_coverage
                WHERE source_key = ?
                LIMIT 1
                """,
                (_COVERAGE_KEY,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        try:
            return VoximplantCoverage(
                window_start=_timestamp(row[0]),
                window_end=_timestamp(row[1]),
                last_run_id=int(row[2]),
            )
        except (TypeError, ValueError):
            return None

    async def save(
        self,
        *,
        window_start: str,
        window_end: str,
        api_total: int,
        facts: tuple[
            VoximplantStatisticFact,
            ...,
        ],
        unique_statistic_ids: int,
        unique_call_ids: int,
        successful_calls: int,
        successful_with_duration: int,
        policy_candidate_calls: int,
        crm_linked_calls: int,
        end_event_matches: int,
        missing_end_events: int,
        successful_start_matches: int,
        successful_missing_start_events: int,
        orphan_start_events: int,
        orphan_end_events: int,
        pagination_complete: bool,
        realtime_complete: bool,
    ) -> int:
        await self.initialize()

        async with aiosqlite.connect(
            self.database_path
        ) as database:
            await _prepare(database)

            await database.execute(
                "BEGIN IMMEDIATE"
            )

            try:
                cursor = await database.execute(
                    """
                    INSERT INTO
                        rop_voximplant_reconciliation_runs (
                            window_start,
                            window_end,
                            api_total,
                            fetched_rows,
                            unique_statistic_ids,
                            unique_call_ids,
                            successful_calls,
                            successful_with_duration,
                            policy_candidate_calls,
                            crm_linked_calls,
                            end_event_matches,
                            missing_end_events,
                            successful_start_matches,
                            successful_missing_start_events,
                            orphan_start_events,
                            orphan_end_events,
                            pagination_complete,
                            realtime_complete
                        )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        window_start,
                        window_end,
                        api_total,
                        len(facts),
                        unique_statistic_ids,
                        unique_call_ids,
                        successful_calls,
                        successful_with_duration,
                        policy_candidate_calls,
                        crm_linked_calls,
                        end_event_matches,
                        missing_end_events,
                        successful_start_matches,
                        successful_missing_start_events,
                        orphan_start_events,
                        orphan_end_events,
                        1
                        if pagination_complete
                        else 0,
                        1
                        if realtime_complete
                        else 0,
                    ),
                )

                run_id = int(
                    cursor.lastrowid
                )

                await database.executemany(
                    """
                    INSERT INTO
                        rop_voximplant_statistic_facts (
                            statistic_id,
                            call_id,
                            call_start_at,
                            call_failed_code,
                            call_duration_seconds,
                            crm_activity_id,
                            crm_entity_type,
                            crm_entity_id,
                            portal_user_id,
                            call_type,
                            last_seen_run_id
                        )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(statistic_id)
                    DO UPDATE SET
                        call_id =
                            excluded.call_id,
                        call_start_at =
                            excluded.call_start_at,
                        call_failed_code =
                            excluded.call_failed_code,
                        call_duration_seconds =
                            excluded.call_duration_seconds,
                        crm_activity_id =
                            excluded.crm_activity_id,
                        crm_entity_type =
                            excluded.crm_entity_type,
                        crm_entity_id =
                            excluded.crm_entity_id,
                        portal_user_id =
                            excluded.portal_user_id,
                        call_type =
                            excluded.call_type,
                        last_seen_run_id =
                            excluded.last_seen_run_id,
                        updated_at =
                            CURRENT_TIMESTAMP
                    """,
                    [
                        (
                            item.statistic_id,
                            item.call_id,
                            item.call_start_at,
                            item.call_failed_code
                            or None,
                            item.call_duration_seconds,
                            item.crm_activity_id
                            or None,
                            item.crm_entity_type
                            or None,
                            item.crm_entity_id
                            or None,
                            item.portal_user_id
                            or None,
                            item.call_type
                            or None,
                            run_id,
                        )
                        for item in facts
                    ],
                )

                if pagination_complete:
                    cursor = await database.execute(
                        """
                        SELECT
                            window_start,
                            window_end
                        FROM rop_voximplant_coverage
                        WHERE source_key = ?
                        LIMIT 1
                        """,
                        (_COVERAGE_KEY,),
                    )
                    coverage_row = await cursor.fetchone()
                    new_start = _timestamp(window_start)
                    new_end = _timestamp(window_end)

                    if coverage_row is None:
                        merged_start = new_start
                        merged_end = new_end
                    else:
                        current_start = _timestamp(coverage_row[0])
                        current_end = _timestamp(coverage_row[1])

                        if (
                            new_start <= current_end
                            and new_end >= current_start
                        ):
                            merged_start = min(current_start, new_start)
                            merged_end = max(current_end, new_end)
                        elif new_end > current_end:
                            # A disjoint newer interval cannot truthfully bridge
                            # the gap, so it becomes the current coverage island.
                            merged_start = new_start
                            merged_end = new_end
                        else:
                            merged_start = current_start
                            merged_end = current_end

                    await database.execute(
                        """
                        INSERT INTO rop_voximplant_coverage (
                            source_key,
                            window_start,
                            window_end,
                            last_run_id,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(source_key)
                        DO UPDATE SET
                            window_start = excluded.window_start,
                            window_end = excluded.window_end,
                            last_run_id = excluded.last_run_id,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            _COVERAGE_KEY,
                            merged_start.isoformat(),
                            merged_end.isoformat(),
                            run_id,
                        ),
                    )

                await database.commit()

                return run_id

            except Exception:
                await database.rollback()
                raise
