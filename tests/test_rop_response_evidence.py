from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.rop_response_evidence import (
    build_lead_response_evidence_report,
    format_lead_response_evidence_for_ai,
)
from app.storage.crm_store import CrmStore


@pytest.mark.asyncio
async def test_response_evidence_report_is_observed_fact_not_sla(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    database_path = str(tmp_path / "agency.db")
    store = CrmStore(database_path)
    await store.initialize()

    created_one = now - timedelta(hours=4)
    created_two = now - timedelta(hours=3)

    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "1",
                "DATE_CREATE": created_one.isoformat(),
                "STATUS_SEMANTIC_ID": "P",
            },
            {
                "ID": "2",
                "DATE_CREATE": created_two.isoformat(),
                "STATUS_SEMANTIC_ID": "P",
            },
        ],
    )

    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "11",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 6,
                "COMPLETED": "Y",
                "END_TIME": (created_one + timedelta(minutes=5)).isoformat(),
            },
            {
                "ID": "12",
                "OWNER_TYPE_ID": 1,
                "OWNER_ID": "1",
                "TYPE_ID": 4,
                "DIRECTION": 1,
                "COMPLETED": "Y",
                "END_TIME": (created_one + timedelta(minutes=10)).isoformat(),
            },
        ],
    )

    settings = Settings(_env_file=None, database_path=database_path)
    report = await build_lead_response_evidence_report(
        settings,
        1,
        now=now,
    )

    assert report.total_leads == 2
    assert report.leads_with_manager_evidence == 1
    assert report.leads_with_confirmed_communication == 1
    assert report.manager_evidence_median_seconds == 300
    assert report.communication_median_seconds == 600
    assert report.manager_evidence_p90_seconds == 300
    assert report.communication_p90_seconds == 600

    text = format_lead_response_evidence_for_ai(report)
    assert "observed CRM evidence" in text
    assert "не First Response SLA" in text
    assert "никаких SLA thresholds" in text
    assert "успел" not in text.lower()
