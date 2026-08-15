from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.rop_deep_analytics import (
    build_loss_report,
    build_manager_report,
    build_stage_aging_report,
    format_loss_report,
    format_manager_report,
    format_stage_aging_report,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_deep_analytics_use_real_final_stages_and_movement(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "user",
        [
            {"ID": "10", "NAME": "Иван", "LAST_NAME": "Петров", "ACTIVE": True},
            {"ID": "11", "NAME": "Анна", "LAST_NAME": "Смирнова", "ACTIVE": True},
        ],
    )
    deals = [
        {
            "ID": "1",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:EXECUTING",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "10",
            "OPPORTUNITY": "100000",
            "CURRENCY_ID": "RUB",
            "MOVED_TIME": "2026-08-01T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-01T10:00:00+00:00",
        },
        {
            "ID": "2",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:EXECUTING",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "10",
            "OPPORTUNITY": "200000",
            "CURRENCY_ID": "RUB",
            "MOVED_TIME": "2026-08-06T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-06T10:00:00+00:00",
        },
        {
            "ID": "3",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:UC_O89RHD",
            "STAGE_SEMANTIC_ID": "F",
            "ASSIGNED_BY_ID": "11",
            "OPPORTUNITY": "50000",
            "CURRENCY_ID": "RUB",
            "MOVED_TIME": "2026-08-05T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-05T10:00:00+00:00",
        },
        {
            "ID": "4",
            "CATEGORY_ID": "2",
            "STAGE_ID": "C2:WON",
            "STAGE_SEMANTIC_ID": "S",
            "ASSIGNED_BY_ID": "10",
            "OPPORTUNITY": "1000",
            "CURRENCY_ID": "USD",
            "MOVED_TIME": "2026-08-04T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-04T10:00:00+00:00",
        },
        {
            "ID": "5",
            "CATEGORY_ID": "2",
            "STAGE_ID": "C2:UC_4UGCIB",
            "STAGE_SEMANTIC_ID": "F",
            "ASSIGNED_BY_ID": "10",
            "OPPORTUNITY": "700",
            "CURRENCY_ID": "USD",
            "MOVED_TIME": "2026-08-03T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-03T10:00:00+00:00",
        },
    ]
    await store.upsert_entities("deal", deals, modified_field="DATE_MODIFY")
    now = datetime(2026, 8, 7, 14, 0, tzinfo=UTC)

    losses = await build_loss_report(database_path, now=now, timezone_name="UTC")
    assert losses.total_lost == 2
    assert losses.reasons[0].count == 1

    aging = await build_stage_aging_report(
        database_path,
        now=now,
        attention_days=3,
        critical_days=5,
    )
    assert aging.active_total == 2
    executing = aging.stages[0]
    assert executing.stage_id == "C7:EXECUTING"
    assert executing.active_count == 2
    assert executing.critical_count == 1

    managers = await build_manager_report(
        database_path,
        now=now,
        timezone_name="UTC",
        attention_days=3,
        critical_days=5,
    )
    manager_10 = next(item for item in managers.managers if item.assigned_by_id == "10")
    assert manager_10.active_count == 2
    assert manager_10.critical_count == 1
    assert manager_10.month_won == 1
    assert manager_10.month_lost == 1
    assert manager_10.month_won_amounts == (("USD", Decimal("1000")),)

    assert "Высокая цена" in format_loss_report(losses)
    assert "stage aging" in format_stage_aging_report(aging)
    manager_text = format_manager_report(managers)
    assert "ID 10" in manager_text
    assert "общий aging" in manager_text
    assert "ФИО пока не подставляются" not in manager_text


@pytest.mark.asyncio
async def test_deep_analytics_excludes_non_directory_human_attribution(tmp_path: Path) -> None:
    database_path = str(tmp_path / "human_deep.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "user",
        [{"ID": "10", "NAME": "Иван", "LAST_NAME": "Петров", "ACTIVE": True}],
    )
    now = datetime(2026, 8, 7, 14, 0, tzinfo=UTC)
    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "1",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:UC_O89RHD",
                "STAGE_SEMANTIC_ID": "F",
                "ASSIGNED_BY_ID": "10",
                "MOVED_TIME": "2026-08-05T10:00:00+00:00",
                "DATE_MODIFY": "2026-08-05T10:00:00+00:00",
            },
            {
                "ID": "2",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:UC_O89RHD",
                "STAGE_SEMANTIC_ID": "F",
                "ASSIGNED_BY_ID": "7912",
                "MOVED_TIME": "2026-08-05T10:00:00+00:00",
                "DATE_MODIFY": "2026-08-05T10:00:00+00:00",
            },
            {
                "ID": "3",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:EXECUTING",
                "STAGE_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "10",
                "MOVED_TIME": "2026-08-01T10:00:00+00:00",
                "DATE_MODIFY": "2026-08-01T10:00:00+00:00",
            },
            {
                "ID": "4",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:EXECUTING",
                "STAGE_SEMANTIC_ID": "P",
                "ASSIGNED_BY_ID": "7912",
                "MOVED_TIME": "2026-08-01T10:00:00+00:00",
                "DATE_MODIFY": "2026-08-01T10:00:00+00:00",
            },
        ],
        modified_field="DATE_MODIFY",
    )

    losses = await build_loss_report(database_path, now=now, timezone_name="UTC")
    assert losses.total_lost == 2
    assert dict(losses.by_manager) == {"10": 1}
    assert dict(losses.excluded_by_manager) == {"7912": 1}

    managers = await build_manager_report(database_path, now=now, timezone_name="UTC")
    assert [item.assigned_by_id for item in managers.managers] == ["10"]
    assert [item.assigned_by_id for item in managers.excluded_attribution] == ["7912"]
    assert sum(item.active_count for item in managers.managers + managers.excluded_attribution) == 2

    loss_text = format_loss_report(losses)
    manager_text = format_manager_report(managers)
    assert "Исключённая атрибуция" in loss_text
    assert "actor ID 7912" in loss_text
    assert "actor ID 7912" in manager_text
