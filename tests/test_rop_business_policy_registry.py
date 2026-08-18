from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from app.config import Settings
from app.services import rop_business_policy_registry as registry
from app.services.rop_business_policy_registry import (
    BUSINESS_POLICY_KEYS,
    ApprovalStatus,
    BindingState,
    build_business_policy_registry,
    format_business_policy_registry_for_ai,
    load_business_policy_document,
)
from app.services.rop_fact_quality import CoverageFact, FactQualityReport


def _config_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policies": {
            key: {
                "approval_status": "pending",
                "approved_by": "",
                "approved_at": "",
                "parameters": {},
                "note": "",
            }
            for key in BUSINESS_POLICY_KEYS
        },
    }


def test_default_registry_file_reflects_customer_questionnaire() -> None:
    document = load_business_policy_document("config/rop-business-policies.json")

    assert document.valid is True
    assert document.schema_version == 1
    assert tuple(item.key for item in document.policies) == BUSINESS_POLICY_KEYS

    by_key = {item.key: item for item in document.policies}

    assert by_key["first_response_sla"].approval_status is ApprovalStatus.APPROVED
    assert by_key["stale_deal"].approval_status is ApprovalStatus.APPROVED
    assert by_key["proposal_stale"].approval_status is ApprovalStatus.APPROVED
    assert by_key["business_conversion"].approval_status is ApprovalStatus.APPROVED

    assert by_key["manager_rating"].approval_status is ApprovalStatus.PENDING
    assert by_key["sales_plan_fact"].approval_status is ApprovalStatus.PENDING
    assert by_key["management_escalation"].approval_status is ApprovalStatus.PENDING

    assert by_key["first_response_sla"].parameters["threshold_seconds"] == 900


def test_registry_fails_closed_on_missing_or_unexpected_policy(tmp_path) -> None:
    payload = _config_payload()
    policies = payload["policies"]
    assert isinstance(policies, dict)

    policies.pop("manager_rating")
    policies["invented_policy"] = {
        "approval_status": "pending",
        "approved_by": "",
        "approved_at": "",
        "parameters": {},
        "note": "",
    }

    path = tmp_path / "policies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    document = load_business_policy_document(str(path))

    assert document.valid is False
    assert document.policies == ()
    assert any("missing_policy_keys:manager_rating" in item for item in document.blockers)
    assert any("unexpected_policy_keys:invented_policy" in item for item in document.blockers)


async def test_approved_business_status_does_not_activate_rule(
    tmp_path,
    monkeypatch,
) -> None:
    payload = _config_payload()
    policies = payload["policies"]
    assert isinstance(policies, dict)

    first_response = policies["first_response_sla"]
    assert isinstance(first_response, dict)
    first_response.update(
        {
            "approval_status": "approved",
            "approved_by": "business-owner",
            "approved_at": "2026-08-14T09:00:00+03:00",
            "parameters": {"threshold_seconds": 900},
        }
    )

    path = tmp_path / "policies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    async def fake_quality(_database_path: str) -> FactQualityReport:
        dependencies = {
            dependency
            for values in registry._POLICY_COVERAGE_DEPENDENCIES.values()
            for dependency in values
        }
        return FactQualityReport(
            generated_at=datetime.now(UTC),
            deal_count=1,
            lead_count=1,
            sales_activity_count=1,
            actor_ids_observed=1,
            actor_ids_resolved=1,
            coverages=tuple(
                CoverageFact(key=key, covered=1, total=1) for key in sorted(dependencies)
            ),
            activity_classes=(),
            notes=(),
        )

    monkeypatch.setattr(registry, "build_fact_quality_report", fake_quality)
    monkeypatch.setattr(
        registry,
        "build_first_response_policy",
        lambda _settings: SimpleNamespace(state=SimpleNamespace(value="ready")),
    )

    settings = Settings(
        _env_file=None,
        database_path="unused.db",
        rop_business_policy_path=str(path),
    )
    snapshot = await build_business_policy_registry(settings)

    first = next(item for item in snapshot.policies if item.key == "first_response_sla")

    assert snapshot.valid is True
    assert first.approval_status is ApprovalStatus.APPROVED
    assert first.binding_state is BindingState.APPROVED_NOT_BOUND
    assert first.operational is False
    assert "technical_binding_not_implemented" in first.blockers


async def test_registry_reports_data_gap_without_quality_verdict(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(_config_payload()), encoding="utf-8")

    async def fake_quality(_database_path: str) -> FactQualityReport:
        return FactQualityReport(
            generated_at=datetime.now(UTC),
            deal_count=1,
            lead_count=10,
            sales_activity_count=10,
            actor_ids_observed=5,
            actor_ids_resolved=4,
            coverages=(
                CoverageFact("lead.created_at", 10, 10),
                CoverageFact("sales_activity.responsible_user_id", 10, 10),
                CoverageFact("sales_activity.observed_timestamp", 10, 10),
                CoverageFact(
                    "observed_actor_id.resolution",
                    4,
                    5,
                ),
            ),
            activity_classes=(),
            notes=(),
        )

    monkeypatch.setattr(registry, "build_fact_quality_report", fake_quality)
    monkeypatch.setattr(
        registry,
        "build_first_response_policy",
        lambda _settings: SimpleNamespace(state=SimpleNamespace(value="disabled")),
    )

    settings = Settings(
        _env_file=None,
        database_path="unused.db",
        rop_business_policy_path=str(path),
    )
    snapshot = await build_business_policy_registry(settings)

    first = next(item for item in snapshot.policies if item.key == "first_response_sla")
    text = format_business_policy_registry_for_ai(snapshot)

    assert first.data_gaps[0].key == "observed_actor_id.resolution"
    assert first.data_gaps[0].missing == 1
    assert "business approval is NOT technical activation" in text
    assert "acceptance threshold" in text
    assert "operational business rules: 0/7" in text
