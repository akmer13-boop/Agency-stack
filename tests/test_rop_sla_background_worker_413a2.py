from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_sla_background_worker import (
    initialize_rop_sla_background_runtime,
    run_rop_sla_background_tick,
    run_rop_sla_background_worker,
)


def settings_for(
    database_path: Path,
    *,
    enabled: bool = False,
) -> Settings:
    return Settings(
        database_path=str(database_path),
        allow_crm_write=False,
        bitrix_event_endpoint_enabled=False,
        rop_sla_worker_enabled=enabled,
        rop_sla_worker_poll_seconds=1,
        rop_sla_worker_event_limit=10,
        rop_sla_worker_deadline_limit=10,
        rop_sla_worker_max_attempts=3,
    )


@pytest.mark.asyncio
async def test_413a2_disabled_worker_has_no_side_effect(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "disabled.db"

    settings = settings_for(
        database_path,
        enabled=False,
    )

    await run_rop_sla_background_worker(
        settings
    )

    assert not database_path.exists()


@pytest.mark.asyncio
async def test_413a2_runtime_initializes_tables_without_coverage(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "runtime.db"

    settings = settings_for(
        database_path
    )

    await initialize_rop_sla_background_runtime(
        settings
    )

    connection = sqlite3.connect(
        database_path
    )

    try:
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }

        required = {
            "bitrix_event_inbox",
            "bitrix_call_evidence",
            "rop_sla_event_dispatch",
            "rop_sla_evaluation_log",
            "rop_sla_deadline_sweep",
            "rop_source_coverage_intervals",
            "rop_operational_coverage_watermarks",
            "rop_lead_policy_profile",
        }

        assert required <= tables

        watermarks = connection.execute(
            """
            SELECT COUNT(*)
            FROM rop_operational_coverage_watermarks
            """
        ).fetchone()[0]

        intervals = connection.execute(
            """
            SELECT COUNT(*)
            FROM rop_source_coverage_intervals
            """
        ).fetchone()[0]

        assert watermarks == 0
        assert intervals == 0

    finally:
        connection.close()


@pytest.mark.asyncio
async def test_413a2_tick_fails_closed_without_verified_coverage(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "tick.db"

    settings = settings_for(
        database_path
    )

    await initialize_rop_sla_background_runtime(
        settings
    )

    result = await run_rop_sla_background_tick(
        settings,
        as_of=datetime(
            2026,
            8,
            21,
            10,
            0,
            tzinfo=UTC,
        ),
    )

    assert result.events_processed == 0
    assert result.event_failures == 0

    assert result.operational_ready is False

    assert result.evaluations_written == 0
    assert result.deadlines_processed == 0

    assert result.missing_sources == (
        "crm_realtime",
        "openlines",
        "voximplant_realtime",
    )

    assert result.lagging_sources == ()

    connection = sqlite3.connect(
        database_path
    )

    try:
        watermarks = connection.execute(
            """
            SELECT COUNT(*)
            FROM rop_operational_coverage_watermarks
            """
        ).fetchone()[0]

        intervals = connection.execute(
            """
            SELECT COUNT(*)
            FROM rop_source_coverage_intervals
            """
        ).fetchone()[0]

        assert watermarks == 0
        assert intervals == 0

    finally:
        connection.close()
