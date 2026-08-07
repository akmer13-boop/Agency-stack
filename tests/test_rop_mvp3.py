from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.rop_deep_analytics import ManagerReport, ManagerStat, format_manager_report
from app.services.rop_mvp3 import (
    build_cycle_time_report,
    build_focus_report,
    build_stage_sla_report,
    format_cycle_time_report,
    format_focus_report,
    format_stage_sla_report,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_mvp3_sla_cycle_and_focus_use_local_crm(tmp_path: Path) -> None:
    now = datetime(2026, 8, 7, 15, 0, tzinfo=UTC)
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()

    deals = [
        {
            "ID": "1",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:PREPARATION",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "10",
            "OPPORTUNITY": "1000",
            "CURRENCY_ID": "RUB",
            "DATE_CREATE": (now - timedelta(days=6)).isoformat(),
            "MOVED_TIME": (now - timedelta(days=4)).isoformat(),
            "DATE_MODIFY": (now - timedelta(days=4)).isoformat(),
        },
        {
            "ID": "2",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:EXECUTING",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "11",
            "OPPORTUNITY": "2000",
            "CURRENCY_ID": "RUB",
            "DATE_CREATE": (now - timedelta(days=20)).isoformat(),
            "MOVED_TIME": (now - timedelta(days=10)).isoformat(),
            "DATE_MODIFY": (now - timedelta(days=10)).isoformat(),
        },
        {
            "ID": "3",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:EXECUTING",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "12",
            "OPPORTUNITY": "3000",
            "CURRENCY_ID": "RUB",
            "DATE_CREATE": (now - timedelta(days=40)).isoformat(),
            "MOVED_TIME": (now - timedelta(days=30)).isoformat(),
            "DATE_MODIFY": (now - timedelta(days=30)).isoformat(),
        },
        {
            "ID": "4",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:WON",
            "STAGE_SEMANTIC_ID": "S",
            "ASSIGNED_BY_ID": "10",
            "OPPORTUNITY": "5000",
            "CURRENCY_ID": "RUB",
            "DATE_CREATE": (now - timedelta(days=12)).isoformat(),
            "MOVED_TIME": (now - timedelta(days=2)).isoformat(),
            "DATE_MODIFY": (now - timedelta(days=2)).isoformat(),
        },
        {
            "ID": "5",
            "CATEGORY_ID": "7",
            "STAGE_ID": "C7:FINAL_INVOICE",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "13",
            "OPPORTUNITY": "4000",
            "CURRENCY_ID": "RUB",
            "DATE_CREATE": (now - timedelta(days=10)).isoformat(),
            "MOVED_TIME": (now - timedelta(days=2)).isoformat(),
            "DATE_MODIFY": (now - timedelta(days=2)).isoformat(),
        },
        {
            "ID": "6",
            "CATEGORY_ID": "8",
            "STAGE_ID": "C8:PREPARATION",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "14",
            "OPPORTUNITY": "0",
            "CURRENCY_ID": "RUB",
            "DATE_CREATE": (now - timedelta(days=5)).isoformat(),
            "MOVED_TIME": (now - timedelta(days=4)).isoformat(),
            "DATE_MODIFY": (now - timedelta(days=4)).isoformat(),
        },
    ]
    histories = [
        {
            "ID": "101",
            "OWNER_ID": "1",
            "STAGE_ID": "C7:PREPARATION",
            "CREATED_TIME": (now - timedelta(hours=100)).isoformat(),
        },
        {
            "ID": "102",
            "OWNER_ID": "4",
            "STAGE_ID": "C7:PREPARATION",
            "CREATED_TIME": (now - timedelta(days=11)).isoformat(),
        },
        {
            "ID": "103",
            "OWNER_ID": "4",
            "STAGE_ID": "C7:EXECUTING",
            "CREATED_TIME": (now - timedelta(days=9)).isoformat(),
        },
        {
            "ID": "104",
            "OWNER_ID": "4",
            "STAGE_ID": "C7:WON",
            "CREATED_TIME": (now - timedelta(days=2)).isoformat(),
        },
        {
            "ID": "105",
            "OWNER_ID": "5",
            "STAGE_ID": "C7:PREPARATION",
            "CREATED_TIME": (now - timedelta(hours=100)).isoformat(),
        },
        {
            "ID": "106",
            "OWNER_ID": "5",
            "STAGE_ID": "C7:FINAL_INVOICE",
            "CREATED_TIME": (now - timedelta(days=2)).isoformat(),
        },
        {
            "ID": "107",
            "OWNER_ID": "6",
            "STAGE_ID": "C8:PREPARATION",
            "CREATED_TIME": (now - timedelta(hours=100)).isoformat(),
        },
    ]
    await store.upsert_entities("deal", deals, modified_field="DATE_MODIFY")
    await store.upsert_entities(
        "deal_stage_history",
        histories,
        modified_field="CREATED_TIME",
    )

    sla = await build_stage_sla_report(database_path, now=now)
    qualification = next(item for item in sla if item.rule.stage_id == "C7:PREPARATION")
    quote = next(item for item in sla if item.rule.stage_id == "C7:EXECUTING")
    assert qualification.active_count == 1
    assert qualification.critical_count == 1
    assert quote.active_count == 2
    assert quote.attention_count == 2
    assert quote.critical_count == 1

    cycle = await build_cycle_time_report(database_path, now=now)
    month_b2c = next(item for item in cycle.month if item.category_id == "7")
    assert month_b2c.sample_count == 1
    assert month_b2c.median_days == pytest.approx(10.0)
    assert month_b2c.p90_days == pytest.approx(10.0)
    assert month_b2c.over_180d_count == 0
    transition = next(item for item in cycle.qualification_to_quote if item.category_id == "7")
    assert transition.completed_count == 1
    assert transition.median_hours == pytest.approx(48.0)
    assert transition.over_72h_count == 0
    assert transition.active_pending_over_72h == 1

    focus = await build_focus_report(database_path, now=now, limit=10)
    assert focus.total_candidates == 4
    assert focus.critical_candidates == 3
    assert focus.monetary_candidates == 3
    assert focus.hygiene_candidates == 1
    assert {item.deal_id for item in focus.deals} == {"1", "2", "3", "6"}
    hygiene = next(item for item in focus.deals if item.deal_id == "6")
    assert hygiene.business_bucket == "hygiene"

    sla_text = format_stage_sla_report(sla)
    assert "72 ч" in sla_text
    assert "% стадии" in sla_text

    cycle_text = format_cycle_time_report(cycle)
    assert "Квалификация → КП" in cycle_text
    assert "сейчас на квалификации без КП >72ч 1" in cycle_text
    assert "WON, закрытые за последние 90 дней" in cycle_text
    assert "p90" in cycle_text
    assert "≥180" in cycle_text

    focus_text = format_focus_report(focus)
    assert "focus-list" in focus_text
    assert "суммой под риском" in focus_text
    assert "гигиена CRM" in focus_text
    assert "разные валюты напрямую не сравниваются" in focus_text


def test_manager_conversion_marks_small_sample() -> None:
    report = ManagerReport(
        managers=(
            ManagerStat(
                assigned_by_id="97",
                active_count=10,
                attention_count=5,
                critical_count=3,
                month_won=1,
                month_lost=2,
                month_won_amounts=(("RUB", Decimal("1000")),),
            ),
        )
    )
    text = format_manager_report(report, min_closed_sample=5)
    assert "малая выборка (n=3)" in text
    assert "33.3%" not in text
