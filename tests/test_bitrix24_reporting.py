from decimal import Decimal

from app.services.bitrix24_reporting import (
    PipelineStageGroup,
    build_deal_summary,
    format_deal_categories,
    format_deal_summary,
    format_pipeline_stages,
    format_recent_deals,
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
        ]
    )

    assert "Новая — NEW [P]" in stages_text
    assert "#15" in deals_text
    assert "12 345.00 RUB" in deals_text
    assert "Не должно отображаться" not in deals_text


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
