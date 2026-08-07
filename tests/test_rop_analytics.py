from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.rop_analytics import (
    RopSnapshot,
    build_rop_snapshot,
    format_rop_funnel,
    format_rop_month,
    format_rop_pipeline,
    format_rop_risks,
    format_rop_today,
    format_rop_week,
)
from app.storage.crm_store import CrmStore


async def _seed_mvp_data(database_path: str) -> None:
    store = CrmStore(database_path)
    await store.initialize()

    deals = [
        {
            "ID": "1",
            "STAGE_SEMANTIC_ID": "P",
            "STAGE_ID": "NEW",
            "CATEGORY_ID": "0",
            "OPPORTUNITY": "100",
            "CURRENCY_ID": "RUB",
            "ASSIGNED_BY_ID": "10",
            "DATE_CREATE": "2026-08-07T13:00:00+00:00",
            "DATE_MODIFY": "2026-08-07T13:30:00+00:00",
            "MOVED_TIME": "2026-08-05T14:30:00+00:00",
        },
        {
            "ID": "2",
            "STAGE_SEMANTIC_ID": "P",
            "STAGE_ID": "QUOTE",
            "CATEGORY_ID": "0",
            "OPPORTUNITY": "200",
            "CURRENCY_ID": "RUB",
            "ASSIGNED_BY_ID": "11",
            "DATE_CREATE": "2026-07-01T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-03T14:00:00+00:00",
            "MOVED_TIME": "2026-08-03T14:00:00+00:00",
        },
        {
            "ID": "3",
            "STAGE_SEMANTIC_ID": "P",
            "STAGE_ID": "NEGOTIATION",
            "CATEGORY_ID": "1",
            "OPPORTUNITY": "1000",
            "CURRENCY_ID": "RUB",
            "ASSIGNED_BY_ID": "12",
            "DATE_CREATE": "2026-06-01T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-01T14:00:00+00:00",
            "MOVED_TIME": "2026-08-01T14:00:00+00:00",
        },
        {
            "ID": "4",
            "STAGE_SEMANTIC_ID": "S",
            "STAGE_ID": "WON",
            "CATEGORY_ID": "0",
            "OPPORTUNITY": "5000",
            "CURRENCY_ID": "RUB",
            "ASSIGNED_BY_ID": "10",
            "DATE_CREATE": "2026-07-01T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-02T14:00:00+00:00",
        },
        {
            "ID": "5",
            "STAGE_SEMANTIC_ID": "F",
            "STAGE_ID": "LOST",
            "CATEGORY_ID": "0",
            "OPPORTUNITY": "3000",
            "CURRENCY_ID": "RUB",
            "ASSIGNED_BY_ID": "13",
            "DATE_CREATE": "2026-07-01T10:00:00+00:00",
            "DATE_MODIFY": "2026-08-02T14:00:00+00:00",
        },
    ]
    leads = [
        {
            "ID": "101",
            "DATE_CREATE": "2026-08-07T13:30:00+00:00",
            "DATE_MODIFY": "2026-08-07T13:40:00+00:00",
        },
        {
            "ID": "102",
            "DATE_CREATE": "2026-07-01T10:00:00+00:00",
            "DATE_MODIFY": "2026-07-01T10:00:00+00:00",
        },
    ]

    await store.upsert_entities("deal", deals, modified_field="DATE_MODIFY")
    await store.upsert_entities("lead", leads, modified_field="DATE_MODIFY")


@pytest.mark.asyncio
async def test_rop_snapshot_calculates_local_mvp_metrics(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    await _seed_mvp_data(database_path)

    snapshot = await build_rop_snapshot(
        database_path,
        now=datetime(2026, 8, 7, 14, 0, tzinfo=UTC),
        attention_days=3,
        critical_days=5,
        risk_limit=10,
        timezone_name="Europe/Moscow",
    )

    assert snapshot.deals_total == 5
    assert snapshot.active_deals == 3
    assert snapshot.won_deals == 1
    assert snapshot.lost_deals == 1
    assert snapshot.leads_total == 2
    assert snapshot.new_leads_24h == 1
    assert snapshot.new_deals_24h == 1
    assert snapshot.closed_conversion_percent == Decimal("50")
    assert snapshot.attention_3d == 2
    assert snapshot.critical_5d == 1
    assert snapshot.risks[0].deal_id == "3"
    assert snapshot.risks[0].idle_days == 6

    rub = next(item for item in snapshot.currencies if item.currency == "RUB")
    assert rub.active_pipeline == Decimal("1300")
    assert rub.won_revenue == Decimal("5000")
    assert rub.average_won_check == Decimal("5000")

    today = snapshot.period("today")
    assert today is not None
    assert today.new_leads == 1
    assert today.new_deals == 1
    assert today.won_deals == 0
    assert today.lost_deals == 0

    week = snapshot.period("week")
    assert week is not None
    assert week.won_deals == 1
    assert week.lost_deals == 1
    assert week.conversion_percent == Decimal("50")
    assert week.won_revenue_by_currency == (("RUB", Decimal("5000")),)

    month = snapshot.period("month")
    assert month is not None
    assert month.won_deals == 1
    assert month.lost_deals == 1


@pytest.mark.asyncio
async def test_rop_snapshot_supports_business_scope_filters(tmp_path: Path) -> None:
    database_path = str(tmp_path / "scope.db")
    await _seed_mvp_data(database_path)

    snapshot = await build_rop_snapshot(
        database_path,
        now=datetime(2026, 8, 7, 14, 0, tzinfo=UTC),
        included_category_ids=frozenset({"0"}),
        excluded_stage_ids=frozenset({"QUOTE"}),
    )

    assert snapshot.deals_total == 3
    assert snapshot.active_deals == 1
    assert snapshot.won_deals == 1
    assert snapshot.lost_deals == 1
    assert snapshot.category_counts == (("0", 3),)
    assert all(stage_id != "QUOTE" for stage_id, _count in snapshot.stage_counts)


@pytest.mark.asyncio
async def test_rop_snapshot_is_safe_on_empty_database(tmp_path: Path) -> None:
    snapshot = await build_rop_snapshot(
        str(tmp_path / "empty.db"),
        now=datetime(2026, 8, 7, 14, 0, tzinfo=UTC),
    )

    assert snapshot.deals_total == 0
    assert snapshot.leads_total == 0
    assert snapshot.closed_conversion_percent == Decimal("0")
    assert snapshot.risks == ()
    assert snapshot.period("today") is not None


def test_rop_formatters_explain_local_calculation() -> None:
    snapshot = RopSnapshot(
        generated_at=datetime(2026, 8, 7, 14, 0, tzinfo=UTC),
        timezone_name="Europe/Moscow",
        deals_total=0,
        active_deals=0,
        won_deals=0,
        lost_deals=0,
        leads_total=0,
        new_leads_24h=0,
        new_deals_24h=0,
        closed_conversion_percent=Decimal("0"),
        attention_3d=0,
        critical_5d=0,
        currencies=(),
        stage_counts=(),
        category_counts=(),
        risks=(),
        periods=(),
    )

    assert "SQLite" in format_rop_today(snapshot)
    assert "распределение сделок" in format_rop_funnel(snapshot)
    assert "Критические" in format_rop_risks(snapshot)
    assert "pipeline" in format_rop_pipeline(snapshot)
    assert "не рассчитан" in format_rop_week(snapshot)
    assert "не рассчитан" in format_rop_month(snapshot)
