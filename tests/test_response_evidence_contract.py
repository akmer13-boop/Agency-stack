from datetime import UTC, datetime, timedelta

from app.semantic.normalizer import normalize_activity, normalize_lead
from app.semantic.response_evidence import build_response_evidence_contract


def test_response_contract_separates_manager_and_communication_evidence() -> None:
    created = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    lead = normalize_lead(
        {
            "ID": "100",
            "DATE_CREATE": created.isoformat(),
            "STATUS_SEMANTIC_ID": "P",
        }
    )

    activities = [
        normalize_activity(
            {
                "ID": "1",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "100",
                "TYPE_ID": 4,
                "DIRECTION": 1,
                "COMPLETED": "Y",
                "END_TIME": (created + timedelta(minutes=10)).isoformat(),
            }
        ),
        normalize_activity(
            {
                "ID": "2",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "100",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (created + timedelta(minutes=5)).isoformat(),
            }
        ),
        normalize_activity(
            {
                "ID": "3",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "100",
                "TYPE_ID": 2,
                "DIRECTION": 2,
                "COMPLETED": "Y",
                "END_TIME": (created + timedelta(minutes=30)).isoformat(),
            }
        ),
    ]

    contract = build_response_evidence_contract(
        [lead],
        activities,
        period_start=created - timedelta(minutes=1),
        observed_until=created + timedelta(hours=1),
    )

    assert contract.cohort_size == 1
    item = contract.leads[0]
    assert item.first_manager_evidence_activity_id == "2"
    assert item.manager_evidence_elapsed_seconds == 300
    assert item.first_confirmed_communication_activity_id == "1"
    assert item.confirmed_communication_elapsed_seconds == 600
    assert item.first_manager_timestamp_source == "END_TIME"
    assert item.first_communication_timestamp_source == "END_TIME"


def test_response_contract_ignores_pre_creation_activity() -> None:
    created = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    lead = normalize_lead({"ID": "100", "DATE_CREATE": created.isoformat()})
    activity = normalize_activity(
        {
            "ID": "1",
            "OWNER_TYPE_ID": 1,
            "OWNER_ID": "100",
            "TYPE_ID": 2,
            "DIRECTION": 2,
            "COMPLETED": "Y",
            "END_TIME": (created - timedelta(minutes=1)).isoformat(),
        }
    )

    contract = build_response_evidence_contract(
        [lead],
        [activity],
        period_start=created - timedelta(minutes=1),
        observed_until=created + timedelta(hours=1),
    )

    item = contract.leads[0]
    assert item.first_manager_evidence_at is None
    assert item.first_confirmed_communication_at is None
    assert item.ignored_pre_creation_activities == 1


def test_response_contract_reports_timestamp_fallback() -> None:
    created = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
    lead = normalize_lead({"ID": "100", "DATE_CREATE": created.isoformat()})
    activity = normalize_activity(
        {
            "ID": "1",
            "OWNER_TYPE_ID": 1,
            "OWNER_ID": "100",
            "TYPE_ID": 6,
            "COMPLETED": "Y",
            "LAST_UPDATED": (created + timedelta(minutes=15)).isoformat(),
        }
    )

    contract = build_response_evidence_contract(
        [lead],
        [activity],
        period_start=created - timedelta(minutes=1),
        observed_until=created + timedelta(hours=1),
    )

    item = contract.leads[0]
    assert item.first_manager_timestamp_source == "LAST_UPDATED"
    assert "manager_timestamp_fallback=LAST_UPDATED" in item.warnings


def test_response_contract_requires_timezone_aware_window() -> None:
    lead = normalize_lead(
        {
            "ID": "100",
            "DATE_CREATE": "2026-08-10T08:00:00+00:00",
        }
    )

    try:
        build_response_evidence_contract(
            [lead],
            [],
            period_start=datetime(2026, 8, 10, 0, 0),
            observed_until=datetime(2026, 8, 10, 10, 0, tzinfo=UTC),
        )
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive period_start must be rejected")
