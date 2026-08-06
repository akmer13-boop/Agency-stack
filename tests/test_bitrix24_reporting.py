from datetime import datetime, timezone
from decimal import Decimal

from app.services.bitrix24_reporting import (
    PipelineStageGroup,
    build_deal_summary,
    build_user_directory,
    find_stuck_deals,
    find_unassigned_cards,
    format_deal_categories,
    format_deal_summary,
    format_demo_leads,
    format_pipeline_stages,
    format_recent_deals,
    format_stuck_deals,
    format_unassigned_cards,
)


def test_deal_summary_counts_statuses_responsible_and_currency() -> None:
    summary = build_deal_summary(
        [
            {
                "ID": "1",
                "CATEGORY_ID": "0",
                "STAGE_ID": "NEW",
                "STAGE_SEMANTIC_ID": "P",
                "OPPORTUNITY": "1000.50",
                "CURRENCY_ID": "RUB",
                "ASSIGNED_BY_ID": "7",
            },
            {
                "ID": "2",
                "CATEGORY_ID": "0",
                "STAGE_ID": "WON",
                "STAGE_SEMANTIC_ID": "S",
                "OPPORTUNITY": "200",
                "CURRENCY_ID": "RUB",
                "ASSIGNED_BY_ID": "",
            },
            {
                "ID": "3",
                "CATEGORY_ID": "2",
                "STAGE_ID": "LOSE",
                "STAGE_SEMANTIC_ID": "F",
                "OPPORTUNITY": "50",
                "CURRENCY_ID": "USD",
                "ASSIGNED_BY_ID": "9",
            },
        ]
    )

    assert summary.total == 3
    assert summary.active == 1
    assert summary.won == 1
    assert summary.lost == 1
    assert summary.without_responsible == 1
    assert dict(summary.opportunity_by_currency) == {
        "RUB": Decimal("1200.50"),
        "USD": Decimal("50"),
    }


def test_format_categories_supports_bitrix_lowercase_fields() -> None:
    text = format_deal_categories(
        [
            {"id": "0", "name": "Основная", "isDefault": True},
            {"id": "2", "name": "VIP", "isDefault": False},
        ]
    )

    assert "Основная (ID 0) — основная" in text
    assert "VIP (ID 2)" in text


def test_format_stages_and_deals_uses_only_analytic_fields() -> None:
    stages_text = format_pipeline_stages(
        (
            PipelineStageGroup(
                category_id=0,
                category_name="Основная",
                stages=(
                    {
                        "STATUS_ID": "NEW",
                        "NAME": "Новая",
                        "SORT": "10",
                        "SEMANTICS": "P",
                    },
                ),
            ),
        )
    )
    deals_text = format_recent_deals(
        [
            {
                "ID": "15",
                "CATEGORY_ID": "0",
                "STAGE_ID": "NEW",
                "OPPORTUNITY": "12345",
                "CURRENCY_ID": "RUB",
                "ASSIGNED_BY_ID": "7",
                "DATE_MODIFY": "2026-08-06T12:00:00+03:00",
                "COMMENTS": "Не должно отображаться",
            }
        ],
        {"7": "Иван Иванов"},
    )

    assert "Новая — NEW [P]" in stages_text
    assert "#15" in deals_text
    assert "12 345.00 RUB" in deals_text
    assert "Иван Иванов" in deals_text
    assert "Не должно отображаться" not in deals_text


def test_user_directory_and_demo_lead_formatting() -> None:
    users = build_user_directory(
        [{"ID": "7", "NAME": "Анна", "LAST_NAME": "Тестова"}]
    )
    text = format_demo_leads(
        [
            {
                "ID": "11",
                "TITLE": "Игрушечный лид",
                "STATUS_ID": "NEW",
                "SOURCE_ID": "WEB",
                "ASSIGNED_BY_ID": "7",
                "DATE_MODIFY": "2026-08-06T12:00:00+03:00",
            }
        ],
        users,
    )

    assert users == {"7": "Анна Тестова"}
    assert "Игрушечный лид" in text
    assert "Анна Тестова" in text
    assert "игрушечного Bitrix24" in text


def test_stuck_deals_excludes_closed_and_sorts_by_inactivity() -> None:
    deals = [
        {
            "ID": "1",
            "STAGE_ID": "NEW",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "7",
            "DATE_MODIFY": "2026-07-20T12:00:00+00:00",
        },
        {
            "ID": "2",
            "STAGE_ID": "WORK",
            "STAGE_SEMANTIC_ID": "P",
            "ASSIGNED_BY_ID": "8",
            "DATE_MODIFY": "2026-08-05T12:00:00+00:00",
        },
        {
            "ID": "3",
            "STAGE_ID": "WON",
            "STAGE_SEMANTIC_ID": "S",
            "DATE_MODIFY": "2026-07-01T12:00:00+00:00",
        },
    ]

    stuck = find_stuck_deals(
        deals,
        stale_days=3,
        users={"7": "Менеджер Один", "8": "Менеджер Два"},
        now=datetime(2026, 8, 6, 12, tzinfo=timezone.utc),
    )
    text = format_stuck_deals(stuck, stale_days=3)

    assert [item.deal["ID"] for item in stuck] == ["1"]
    assert "Менеджер Один" in text
    assert "в OpenAI не передавались" in text


def test_unassigned_cards_are_detected_locally() -> None:
    deals, leads = find_unassigned_cards(
        [{"ID": "1", "ASSIGNED_BY_ID": ""}, {"ID": "2", "ASSIGNED_BY_ID": "7"}],
        [{"ID": "9", "ASSIGNED_BY_ID": "0"}],
    )
    text = format_unassigned_cards(deals, leads)

    assert [item["ID"] for item in deals] == ["1"]
    assert [item["ID"] for item in leads] == ["9"]
    assert "сделки: 1" in text
    assert "лиды: 1" in text


def test_summary_text_marks_local_processing() -> None:
    summary = build_deal_summary(
        [
            {
                "STAGE_SEMANTIC_ID": "P",
                "CATEGORY_ID": "0",
                "STAGE_ID": "NEW",
                "OPPORTUNITY": "100",
                "CURRENCY_ID": "RUB",
                "ASSIGNED_BY_ID": "7",
            }
        ]
    )

    text = format_deal_summary(summary)

    assert "активные: 1" in text
    assert "RUB: 100.00" in text
    assert "в OpenAI не передавались" in text
