from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_response_trend import (
    build_response_evidence_trend,
    format_response_evidence_trend_for_ai,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_response_trend_compares_mature_calendar_weeks(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "user",
        [{"ID": "10", "NAME": "Иван", "LAST_NAME": "Петров", "ACTIVE": True}],
    )

    previous_start = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    current_start = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)

    leads = [
        {"ID": "1", "DATE_CREATE": (previous_start + timedelta(days=1)).isoformat()},
        {"ID": "2", "DATE_CREATE": (previous_start + timedelta(days=2)).isoformat()},
        {"ID": "3", "DATE_CREATE": (current_start + timedelta(days=1)).isoformat()},
        {"ID": "4", "DATE_CREATE": (current_start + timedelta(days=2)).isoformat()},
    ]
    await store.upsert_entities("lead", leads)

    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "101",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "RESPONSIBLE_ID": "10",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (previous_start + timedelta(days=1, hours=4)).isoformat(),
            },
            {
                "ID": "102",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 4,
                "DIRECTION": 1,
                "COMPLETED": "Y",
                "END_TIME": (previous_start + timedelta(days=1, hours=5)).isoformat(),
            },
            {
                "ID": "103",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "2",
                "RESPONSIBLE_ID": "10",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (previous_start + timedelta(days=10)).isoformat(),
            },
            {
                "ID": "201",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "3",
                "RESPONSIBLE_ID": "10",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (current_start + timedelta(days=1, hours=1)).isoformat(),
            },
            {
                "ID": "202",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "4",
                "RESPONSIBLE_ID": "10",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (current_start + timedelta(days=2, hours=2)).isoformat(),
            },
            {
                "ID": "203",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "3",
                "TYPE_ID": 4,
                "DIRECTION": 1,
                "COMPLETED": "Y",
                "END_TIME": (current_start + timedelta(days=1, hours=3)).isoformat(),
            },
            {
                "ID": "204",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "4",
                "TYPE_ID": 4,
                "DIRECTION": 1,
                "COMPLETED": "Y",
                "END_TIME": (current_start + timedelta(days=2, hours=4)).isoformat(),
            },
        ],
    )

    settings = Settings(
        _env_file=None,
        database_path=database_path,
        rop_timezone="UTC",
    )

    report = await build_response_evidence_trend(
        settings,
        weeks=2,
        now=now,
        observation_horizon_days=7,
    )

    assert len(report.cohorts) == 2

    previous, current = report.cohorts
    assert previous.total_leads == 2
    assert previous.manager_evidence_leads == 1
    assert previous.manager_coverage_percent == 50.0
    assert previous.manager_median_seconds == 4 * 60 * 60

    assert current.total_leads == 2
    assert current.manager_evidence_leads == 2
    assert current.manager_coverage_percent == 100.0
    assert current.manager_median_seconds == 90 * 60

    delta = report.latest_vs_previous
    assert delta is not None
    assert delta.manager_coverage_delta_pp == 50.0
    assert delta.manager_coverage_direction == "higher"
    assert delta.manager_median_direction == "faster"

    text = format_response_evidence_trend_for_ai(report)
    assert "observation horizon" in text
    assert "НЕ является SLA threshold" in text
    assert "статистическая значимость" in text
    assert "не First Response SLA" in text


@pytest.mark.asyncio
async def test_response_trend_rejects_invalid_week_count(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "agency.db"),
        rop_timezone="UTC",
    )

    with pytest.raises(ValueError, match="weeks must be from 2 to 12"):
        await build_response_evidence_trend(settings, weeks=1)


@pytest.mark.asyncio
async def test_response_trend_tracks_excluded_non_directory_manager_evidence(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    database_path = str(tmp_path / "trend_human.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "user",
        [{"ID": "10", "NAME": "Иван", "LAST_NAME": "Петров", "ACTIVE": True}],
    )
    previous_start = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    current_start = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    await store.upsert_entities(
        "lead",
        [
            {"ID": "1", "DATE_CREATE": (previous_start + timedelta(days=1)).isoformat()},
            {"ID": "2", "DATE_CREATE": (current_start + timedelta(days=1)).isoformat()},
        ],
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "101",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "RESPONSIBLE_ID": "7912",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (previous_start + timedelta(days=1, minutes=5)).isoformat(),
            },
            {
                "ID": "102",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "RESPONSIBLE_ID": "10",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (previous_start + timedelta(days=1, minutes=15)).isoformat(),
            },
            {
                "ID": "201",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "2",
                "RESPONSIBLE_ID": "10",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (current_start + timedelta(days=1, minutes=10)).isoformat(),
            },
        ],
    )
    settings = Settings(_env_file=None, database_path=database_path, rop_timezone="UTC")
    report = await build_response_evidence_trend(
        settings,
        weeks=2,
        now=now,
        observation_horizon_days=7,
    )
    assert report.cohorts[0].excluded_non_directory_manager_evidence_leads == 1
    assert report.cohorts[0].manager_median_seconds == 15 * 60
    assert report.cohorts[1].manager_median_seconds == 10 * 60
