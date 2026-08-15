from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.rop_management_facts import (
    build_management_facts,
    format_management_facts_for_ai,
)
from app.storage.crm_store import CrmStore, CrmTombstone


@pytest.mark.asyncio
async def test_management_fact_layer_is_active_only_and_policy_free(tmp_path) -> None:
    database_path = str(tmp_path / "facts.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities("department", [{"ID": "7", "NAME": "Продажи"}])
    await store.upsert_entities(
        "user",
        [
            {
                "ID": "1",
                "NAME": "Анна",
                "LAST_NAME": "Иванова",
                "ACTIVE": True,
                "UF_DEPARTMENT": ["7"],
            },
            {
                "ID": "2",
                "NAME": "Пётр",
                "LAST_NAME": "Сидоров",
                "ACTIVE": True,
                "UF_DEPARTMENT": ["7"],
            },
        ],
    )
    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "10",
                "ASSIGNED_BY_ID": "1",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:NEW",
                "STAGE_SEMANTIC_ID": "P",
                "OPPORTUNITY": "100",
                "CURRENCY_ID": "RUB",
            },
            {
                "ID": "11",
                "ASSIGNED_BY_ID": "1",
                "CATEGORY_ID": "7",
                "STAGE_ID": "WON",
                "STAGE_SEMANTIC_ID": "S",
                "OPPORTUNITY": "500",
                "CURRENCY_ID": "RUB",
            },
            {
                "ID": "12",
                "ASSIGNED_BY_ID": "2",
                "CATEGORY_ID": "7",
                "STAGE_ID": "LOSE",
                "STAGE_SEMANTIC_ID": "F",
                "OPPORTUNITY": "250",
                "CURRENCY_ID": "RUB",
            },
            {
                "ID": "13",
                "ASSIGNED_BY_ID": "1",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:NEW",
                "STAGE_SEMANTIC_ID": "P",
                "OPPORTUNITY": "999",
                "CURRENCY_ID": "RUB",
            },
        ],
    )
    await store.upsert_entities(
        "lead",
        [
            {"ID": "20", "ASSIGNED_BY_ID": "1", "STATUS_ID": "NEW", "STATUS_SEMANTIC_ID": "P"},
            {
                "ID": "21",
                "ASSIGNED_BY_ID": "1",
                "STATUS_ID": "CONVERTED",
                "STATUS_SEMANTIC_ID": "S",
            },
            {"ID": "22", "ASSIGNED_BY_ID": "2", "STATUS_ID": "JUNK", "STATUS_SEMANTIC_ID": "F"},
        ],
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "30",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "20",
                "RESPONSIBLE_ID": "1",
                "TYPE_ID": "2",
                "DIRECTION": "2",
                "COMPLETED": "Y",
                "CREATED": "2026-08-10T10:00:00Z",
            },
            {
                "ID": "31",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "10",
                "RESPONSIBLE_ID": "1",
                "TYPE_ID": "6",
                "COMPLETED": "Y",
                "CREATED": "2026-08-10T11:00:00Z",
            },
            {
                "ID": "32",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "12",
                "RESPONSIBLE_ID": "2",
                "TYPE_ID": "5",
                "COMPLETED": "Y",
                "AUTOCOMPLETE_RULE": "1",
                "CREATED": "2026-08-10T12:00:00Z",
            },
            {
                "ID": "33",
                "OWNER_TYPE_ID": "3",
                "OWNER_ID": "999",
                "RESPONSIBLE_ID": "1",
                "TYPE_ID": "2",
                "DIRECTION": "2",
                "COMPLETED": "Y",
                "CREATED": "2026-08-10T13:00:00Z",
            },
        ],
    )
    await store.upsert_entities(
        "deal_stage_history",
        [
            {
                "ID": "40",
                "OWNER_ID": "10",
                "STAGE_ID": "C7:NEW",
                "STAGE_SEMANTIC_ID": "P",
                "CREATED_TIME": "2026-08-10T09:00:00Z",
            }
        ],
    )
    await store.upsert_entities(
        "lead_stage_history",
        [
            {
                "ID": "41",
                "OWNER_ID": "20",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
                "CREATED_TIME": "2026-08-10T09:30:00Z",
            }
        ],
    )
    await store.apply_tombstones(
        [
            CrmTombstone(
                entity_type="deal",
                entity_id="13",
                source_audit_run_id=9,
                evidence_kind="test_confirmed_missing",
                evidence_verified_at="2026-08-13T00:00:00Z",
            )
        ]
    )

    snapshot = await build_management_facts(
        database_path,
        now=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert (snapshot.active_deals, snapshot.won_deals, snapshot.lost_deals) == (1, 1, 1)
    assert (snapshot.active_leads, snapshot.successful_leads, snapshot.failed_leads) == (1, 1, 1)
    assert snapshot.sales_activities_total == 3
    assert snapshot.deal_stage_history_events == 1
    assert snapshot.lead_stage_history_events == 1

    managers = {item.manager_id: item for item in snapshot.managers}
    anna = managers["1"]
    petr = managers["2"]

    assert anna.current_active_deals == 1
    assert anna.current_won_deals == 1
    assert anna.current_lost_deals == 0
    assert anna.current_active_leads == 1
    assert anna.current_success_leads == 1
    assert anna.won_crm_opportunity_by_currency == (("RUB", Decimal("500")),)
    assert anna.sales_activities_total == 2
    assert anna.confirmed_communications == 1
    assert anna.manager_evidence_activities == 2
    assert anna.human_actions == 1

    assert petr.current_lost_deals == 1
    assert petr.current_failed_leads == 1
    assert petr.sales_activities_total == 1
    assert petr.system_activities == 1

    pending = {item.key for item in snapshot.pending_business_rules}
    assert {
        "first_response_sla",
        "stale_deal",
        "proposal_stale",
        "business_conversion",
        "manager_rating",
        "sales_plan_fact",
        "management_escalation",
    } <= pending


@pytest.mark.asyncio
async def test_management_fact_formatter_refuses_business_interpretation(tmp_path) -> None:
    database_path = str(tmp_path / "facts.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "10",
                "ASSIGNED_BY_ID": "1",
                "STAGE_ID": "WON",
                "STAGE_SEMANTIC_ID": "S",
                "OPPORTUNITY": "500",
                "CURRENCY_ID": "RUB",
            }
        ],
    )

    snapshot = await build_management_facts(database_path)
    text = format_management_facts_for_ai(snapshot)

    assert "NOT A RATING / NOT A RANKING" in text
    assert "BUSINESS RULES STILL PENDING — DO NOT CALCULATE" in text
    assert "manager_rating: pending_business_approval" in text
    assert "First Response SLA compliance" in text
    assert "WON CRM OPPORTUNITY" in text
    assert "not proven payment or accounting revenue" in text


@pytest.mark.asyncio
async def test_management_fact_formatter_can_filter_responsible_id(tmp_path) -> None:
    database_path = str(tmp_path / "facts.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "deal",
        [
            {"ID": "10", "ASSIGNED_BY_ID": "1", "STAGE_ID": "C7:NEW", "STAGE_SEMANTIC_ID": "P"},
            {"ID": "11", "ASSIGNED_BY_ID": "2", "STAGE_ID": "C7:NEW", "STAGE_SEMANTIC_ID": "P"},
        ],
    )

    snapshot = await build_management_facts(database_path)
    text = format_management_facts_for_ai(snapshot, manager_id="2")

    assert "(ID 2)" in text
    assert "(ID 1)" not in text


@pytest.mark.asyncio
async def test_management_facts_separate_non_directory_actors_from_human_managers(
    tmp_path,
) -> None:
    database_path = str(tmp_path / "human_guard.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities(
        "user",
        [{"ID": "1", "NAME": "Анна", "ACTIVE": True}],
    )
    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "10",
                "ASSIGNED_BY_ID": "1",
                "STAGE_ID": "C7:NEW",
                "STAGE_SEMANTIC_ID": "P",
            }
        ],
    )
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "20",
                "ASSIGNED_BY_ID": "7912",
                "CREATED_BY_ID": "7912",
                "SOURCE_ID": "5|WZ_TELEGRAM_TEST",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
            },
            {
                "ID": "21",
                "ASSIGNED_BY_ID": "484",
                "STATUS_ID": "NEW",
                "STATUS_SEMANTIC_ID": "P",
            },
        ],
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "30",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "20",
                "RESPONSIBLE_ID": "7912",
                "AUTHOR_ID": "7912",
                "PROVIDER_ID": "IMOPENLINES_SESSION",
                "TYPE_ID": "6",
                "COMPLETED": "Y",
                "CREATED": "2026-08-14T09:00:00Z",
            },
            {
                "ID": "31",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "21",
                "RESPONSIBLE_ID": "484",
                "AUTHOR_ID": "54",
                "PROVIDER_ID": "VOXIMPLANT_CALL",
                "TYPE_ID": "2",
                "COMPLETED": "Y",
                "CREATED": "2026-08-14T09:10:00Z",
            },
        ],
    )

    snapshot = await build_management_facts(
        database_path,
        now=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )

    assert snapshot.active_deals == 1
    assert snapshot.active_leads == 2
    assert snapshot.sales_activities_total == 2
    assert {item.manager_id for item in snapshot.managers} == {"1"}

    excluded = {item.manager_id: item for item in snapshot.excluded_actors}
    assert set(excluded) == {"484", "7912"}
    assert excluded["7912"].actor_kind == "special_actor_candidate"
    assert excluded["484"].actor_kind == "unresolved_actor"
    assert excluded["7912"].current_active_leads == 1
    assert excluded["484"].current_active_leads == 1

    text = format_management_facts_for_ai(snapshot)
    human = text.split(
        "HUMAN MANAGER FACTS — DIRECTORY USERS ONLY — NOT A RATING / NOT A RANKING:"
    )[1].split("NON-HUMAN / UNRESOLVED ATTRIBUTION — NOT MANAGERS:")[0]
    excluded_text = text.split("NON-HUMAN / UNRESOLVED ATTRIBUTION — NOT MANAGERS:")[1]

    assert "(ID 1)" in human
    assert "(ID 7912)" not in human
    assert "(ID 484)" not in human
    assert "(ID 7912)" in excluded_text
    assert "(ID 484)" in excluded_text
