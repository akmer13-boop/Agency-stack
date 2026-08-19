from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

KNOWN_SOURCE_KEYS = frozenset(
    {
        "openlines",
        "voximplant_realtime",
        "crm_realtime",
    }
)


@dataclass(frozen=True, slots=True)
class SourceCoverageInterval:
    interval_id: int
    source_key: str
    coverage_start_ts: int
    coverage_end_ts: int | None
    evidence_kind: str
    complete: bool


@dataclass(frozen=True, slots=True)
class SourceCoverageCheck:
    source_key: str
    complete: bool
    window_start_ts: int
    window_end_ts: int
    intervals_used: int
    covered_until_ts: int | None
    blockers: tuple[str, ...]


def _timestamp(
    value: datetime,
) -> int:
    if value.tzinfo is None:
        raise ValueError("coverage_datetime_must_be_timezone_aware")

    return int(value.astimezone(UTC).timestamp())


class RopSourceCoverageStore:
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
            await database.execute("PRAGMA busy_timeout=5000")

            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS rop_source_coverage_intervals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    coverage_start_ts INTEGER NOT NULL,
                    coverage_end_ts INTEGER,
                    evidence_kind TEXT NOT NULL,
                    complete INTEGER NOT NULL
                        CHECK (
                            complete IN (0, 1)
                        ),
                    recorded_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    CHECK (
                        coverage_end_ts IS NULL
                        OR coverage_end_ts
                           >= coverage_start_ts
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_rop_source_coverage_window
                    ON rop_source_coverage_intervals (
                        source_key,
                        complete,
                        coverage_start_ts,
                        coverage_end_ts
                    );
                """
            )

            await database.commit()

    async def add_interval(
        self,
        *,
        source_key: str,
        coverage_start: datetime,
        coverage_end: datetime | None,
        evidence_kind: str,
        complete: bool = True,
    ) -> int:
        if source_key not in KNOWN_SOURCE_KEYS:
            raise ValueError("unknown_source_key:" + source_key)

        evidence = evidence_kind.strip()

        if not evidence:
            raise ValueError("coverage_evidence_kind_required")

        start_ts = _timestamp(coverage_start)

        end_ts = _timestamp(coverage_end) if coverage_end is not None else None

        if end_ts is not None and end_ts < start_ts:
            raise ValueError("coverage_end_before_start")

        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await database.execute("PRAGMA busy_timeout=5000")

            cursor = await database.execute(
                """
                INSERT INTO rop_source_coverage_intervals (
                    source_key,
                    coverage_start_ts,
                    coverage_end_ts,
                    evidence_kind,
                    complete
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source_key,
                    start_ts,
                    end_ts,
                    evidence,
                    1 if complete else 0,
                ),
            )

            await database.commit()

            return int(cursor.lastrowid)

    async def list_intervals(
        self,
        source_key: str,
    ) -> tuple[SourceCoverageInterval, ...]:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await database.execute("PRAGMA busy_timeout=5000")

            cursor = await database.execute(
                """
                SELECT
                    id,
                    source_key,
                    coverage_start_ts,
                    coverage_end_ts,
                    evidence_kind,
                    complete
                FROM rop_source_coverage_intervals
                WHERE source_key = ?
                ORDER BY
                    coverage_start_ts,
                    id
                """,
                (source_key,),
            )

            rows = await cursor.fetchall()

        return tuple(
            SourceCoverageInterval(
                interval_id=int(row[0]),
                source_key=str(row[1]),
                coverage_start_ts=int(row[2]),
                coverage_end_ts=(int(row[3]) if row[3] is not None else None),
                evidence_kind=str(row[4]),
                complete=bool(row[5]),
            )
            for row in rows
        )

    async def check_window(
        self,
        *,
        source_key: str,
        window_start: datetime,
        window_end: datetime,
    ) -> SourceCoverageCheck:
        if source_key not in KNOWN_SOURCE_KEYS:
            raise ValueError("unknown_source_key:" + source_key)

        start_ts = _timestamp(window_start)

        end_ts = _timestamp(window_end)

        if end_ts < start_ts:
            raise ValueError("coverage_window_end_before_start")

        intervals = tuple(item for item in await self.list_intervals(source_key) if item.complete)

        if not intervals:
            return SourceCoverageCheck(
                source_key=source_key,
                complete=False,
                window_start_ts=start_ts,
                window_end_ts=end_ts,
                intervals_used=0,
                covered_until_ts=None,
                blockers=("source_coverage_missing:" + source_key,),
            )

        cursor_ts = start_ts
        used = 0

        for interval in intervals:
            interval_end = (
                interval.coverage_end_ts if interval.coverage_end_ts is not None else end_ts
            )

            if interval_end < cursor_ts:
                continue

            if interval.coverage_start_ts > cursor_ts:
                break

            used += 1

            cursor_ts = max(
                cursor_ts,
                interval_end,
            )

            if cursor_ts >= end_ts:
                return SourceCoverageCheck(
                    source_key=source_key,
                    complete=True,
                    window_start_ts=start_ts,
                    window_end_ts=end_ts,
                    intervals_used=used,
                    covered_until_ts=cursor_ts,
                    blockers=(),
                )

        return SourceCoverageCheck(
            source_key=source_key,
            complete=False,
            window_start_ts=start_ts,
            window_end_ts=end_ts,
            intervals_used=used,
            covered_until_ts=(cursor_ts if used else None),
            blockers=("source_coverage_gap:" + source_key,),
        )
