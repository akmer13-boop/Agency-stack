from __future__ import annotations

from dataclasses import dataclass
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
                """
            )

            await database.commit()

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
                        ?, ?, ?, ?, ?, ?, ?, ?
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
                            last_seen_run_id
                        )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?
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
                            run_id,
                        )
                        for item in facts
                    ],
                )

                await database.commit()

                return run_id

            except Exception:
                await database.rollback()
                raise
