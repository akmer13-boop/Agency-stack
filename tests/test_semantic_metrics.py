from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.semantic.metrics import build_new_leads_metric
from app.semantic.models import SemanticLead


def _lead(lead_id: str, created_at: datetime | None) -> SemanticLead:
    return SemanticLead(
        id=lead_id,
        assigned_user_id=None,
        created_at=created_at,
        updated_at=None,
        status_id=None,
        status_semantic=None,
        source_id=None,
        amount=Decimal("0"),
        currency=None,
    )


def test_new_leads_metric_has_explicit_contract() -> None:
    start = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    leads = [
        _lead("1", start),
        _lead("2", datetime(2026, 8, 10, 6, 0, tzinfo=UTC)),
        _lead("3", end),
        _lead("4", datetime(2026, 8, 9, 23, 59, tzinfo=UTC)),
    ]

    metric = build_new_leads_metric(
        leads,
        period_start=start,
        period_end=end,
        timezone_name="Europe/Moscow",
        calculated_at=end,
    )

    assert metric.metric == "new_leads"
    assert metric.value == 3
    assert metric.sample_size == 4
    assert metric.source_entity_ids == ("1", "2", "3")
    assert metric.period_start == start
    assert metric.period_end == end
    assert metric.timezone == "Europe/Moscow"
    assert metric.calculated_at == end
    assert metric.warnings == ()


def test_new_leads_metric_reports_missing_created_at() -> None:
    start = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)

    metric = build_new_leads_metric(
        [_lead("1", None)],
        period_start=start,
        period_end=end,
        timezone_name="UTC",
        calculated_at=end,
    )

    assert metric.value == 0
    assert metric.sample_size == 1
    assert metric.warnings == ("leads_without_created_at=1",)


def test_new_leads_metric_rejects_naive_period() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_new_leads_metric(
            [],
            period_start=datetime(2026, 8, 10, 0, 0),
            period_end=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            timezone_name="UTC",
        )


def test_new_leads_metric_rejects_reversed_period() -> None:
    start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    end = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="period_start"):
        build_new_leads_metric(
            [],
            period_start=start,
            period_end=end,
            timezone_name="UTC",
        )
