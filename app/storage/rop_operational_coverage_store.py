from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.storage.rop_source_coverage_store import (
    KNOWN_SOURCE_KEYS,
    RopSourceCoverageStore,
)

REQUIRED_OPERATIONAL_SOURCES = (
    "crm_realtime",
    "openlines",
    "voximplant_realtime",
)


@dataclass(frozen=True, slots=True)
class OperationalCoverageWatermark:
    source_key: str
    coverage_start_ts: int
    covered_through_ts: int
    observed_at_ts: int
    evidence_kind: str


@dataclass(frozen=True, slots=True)
class OperationalCoverageStatus:
    ready: bool
    requested_as_of_ts: int
    minimum_covered_through_ts: int | None
    missing_sources: tuple[str, ...]
    lagging_sources: tuple[str, ...]


def _timestamp(
    value: datetime,
) -> int:
    if value.tzinfo is None:
        raise ValueError("coverage_datetime_must_be_timezone_aware")

    return int(value.astimezone(UTC).timestamp())


async def _prepare(
    database: aiosqlite.Connection,
) -> None:
    await database.execute("PRAGMA busy_timeout=5000")


class RopOperationalCoverageStore:
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
                CREATE TABLE IF NOT EXISTS rop_operational_coverage_watermarks (
                    source_key TEXT PRIMARY KEY,
                    coverage_start_ts INTEGER NOT NULL,
                    covered_through_ts INTEGER NOT NULL,
                    observed_at_ts INTEGER NOT NULL,
                    evidence_kind TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    CHECK (
                        covered_through_ts
                        >= coverage_start_ts
                    ),
                    CHECK (
                        observed_at_ts
                        >= covered_through_ts
                    )
                );
                """
            )

            await database.commit()

    async def initialize_source(
        self,
        *,
        source_key: str,
        coverage_start: datetime,
        covered_through: datetime,
        observed_at: datetime,
        evidence_kind: str,
    ) -> None:
        if source_key not in KNOWN_SOURCE_KEYS:
            raise ValueError("unknown_source_key:" + source_key)

        evidence = evidence_kind.strip()

        if not evidence:
            raise ValueError("coverage_evidence_kind_required")

        start_ts = _timestamp(coverage_start)

        through_ts = _timestamp(covered_through)

        observed_ts = _timestamp(observed_at)

        if through_ts < start_ts:
            raise ValueError("covered_through_before_start")

        if through_ts > observed_ts:
            raise ValueError("covered_through_after_observed_at")

        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                SELECT 1
                FROM rop_operational_coverage_watermarks
                WHERE source_key = ?
                LIMIT 1
                """,
                (source_key,),
            )

            if await cursor.fetchone() is not None:
                raise ValueError("coverage_source_already_initialized:" + source_key)

        coverage = RopSourceCoverageStore(self.database_path)

        await coverage.add_interval(
            source_key=source_key,
            coverage_start=coverage_start,
            coverage_end=covered_through,
            evidence_kind=evidence,
            complete=True,
        )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            await database.execute(
                """
                INSERT INTO rop_operational_coverage_watermarks (
                    source_key,
                    coverage_start_ts,
                    covered_through_ts,
                    observed_at_ts,
                    evidence_kind
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source_key,
                    start_ts,
                    through_ts,
                    observed_ts,
                    evidence,
                ),
            )

            await database.commit()

    async def advance_source(
        self,
        *,
        source_key: str,
        covered_through: datetime,
        observed_at: datetime,
        evidence_kind: str,
    ) -> None:
        if source_key not in KNOWN_SOURCE_KEYS:
            raise ValueError("unknown_source_key:" + source_key)

        evidence = evidence_kind.strip()

        if not evidence:
            raise ValueError("coverage_evidence_kind_required")

        through_ts = _timestamp(covered_through)

        observed_ts = _timestamp(observed_at)

        if through_ts > observed_ts:
            raise ValueError("covered_through_after_observed_at")

        await self.initialize()

        current = await self.get(source_key)

        if current is None:
            raise ValueError("coverage_source_not_initialized:" + source_key)

        if through_ts < current.covered_through_ts:
            raise ValueError("coverage_watermark_regression:" + source_key)

        if through_ts > current.covered_through_ts:
            coverage = RopSourceCoverageStore(self.database_path)

            start = datetime.fromtimestamp(
                current.covered_through_ts,
                tz=UTC,
            )

            await coverage.add_interval(
                source_key=source_key,
                coverage_start=start,
                coverage_end=covered_through,
                evidence_kind=evidence,
                complete=True,
            )

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            await database.execute(
                """
                UPDATE rop_operational_coverage_watermarks
                SET
                    covered_through_ts = ?,
                    observed_at_ts = ?,
                    evidence_kind = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_key = ?
                """,
                (
                    through_ts,
                    observed_ts,
                    evidence,
                    source_key,
                ),
            )

            await database.commit()

    async def get(
        self,
        source_key: str,
    ) -> OperationalCoverageWatermark | None:
        await self.initialize()

        async with aiosqlite.connect(self.database_path) as database:
            await _prepare(database)

            cursor = await database.execute(
                """
                SELECT
                    source_key,
                    coverage_start_ts,
                    covered_through_ts,
                    observed_at_ts,
                    evidence_kind
                FROM rop_operational_coverage_watermarks
                WHERE source_key = ?
                LIMIT 1
                """,
                (source_key,),
            )

            row = await cursor.fetchone()

        if row is None:
            return None

        return OperationalCoverageWatermark(
            source_key=str(row[0]),
            coverage_start_ts=int(row[1]),
            covered_through_ts=int(row[2]),
            observed_at_ts=int(row[3]),
            evidence_kind=str(row[4]),
        )

    async def operational_status(
        self,
        *,
        as_of: datetime,
    ) -> OperationalCoverageStatus:
        requested_ts = _timestamp(as_of)

        missing: list[str] = []
        lagging: list[str] = []
        through_values: list[int] = []

        for source in REQUIRED_OPERATIONAL_SOURCES:
            item = await self.get(source)

            if item is None:
                missing.append(source)
                continue

            through_values.append(item.covered_through_ts)

            if item.covered_through_ts < requested_ts:
                lagging.append(source)

        return OperationalCoverageStatus(
            ready=(not missing and not lagging),
            requested_as_of_ts=requested_ts,
            minimum_covered_through_ts=(min(through_values) if through_values else None),
            missing_sources=tuple(sorted(missing)),
            lagging_sources=tuple(sorted(lagging)),
        )
