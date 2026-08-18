from __future__ import annotations

import json
from pathlib import Path


def binding():
    return json.loads(Path("config/rop-bitrix-bindings.json").read_text(encoding="utf-8"))


def test_410b_lead_binding() -> None:
    data = binding()

    assert data["lead"]["unprocessed"]["status_id"] == "NEW"

    assert data["lead"]["qualified"]["status_id"] == "CONVERTED"


def test_410b_primary_funnel() -> None:
    funnel = binding()["business_policy_funnel"]

    assert funnel["category_id"] == 7

    assert funnel["entity_id"] == "DEAL_STAGE_7"


def test_410b_exact_stage_bindings() -> None:
    stages = binding()["business_policy_funnel"]["stage_sla"]

    assert stages["needs_discovery"]["status_id"] == "C7:PREPARATION"

    assert stages["package_tour_selection"]["status_id"] == "C7:PREPAYMENT_INVOICE"

    assert stages["partner_request"]["status_id"] == "C7:UC_IAVLST"


def test_410b_name_variants_are_business_confirmed() -> None:
    stages = binding()["business_policy_funnel"]["stage_sla"]

    for key in (
        "new_application",
        "proposal_sent",
    ):
        assert stages[key]["needs_business_confirmation"] is False

        assert stages[key]["match_type"] == "business_confirmed_alias"


def test_410b_success_loss_ids() -> None:
    funnel = binding()["business_policy_funnel"]

    success = funnel["successful_sale_stages"]

    lost = funnel["lost_sale_stages"]

    assert success["prepayment_received"]["status_id"] == "C7:UC_9XH4U7"

    assert success["booking_confirmed"]["status_id"] == "C7:UC_TJNUX1"

    assert success["deal_completed"]["status_id"] == "C7:WON"

    assert lost["service_unavailable"]["status_id"] == "C7:LOSE"

    assert lost["slow_response"]["status_id"] == "C7:APOLOGY"


def test_410b_other_funnels_stay_unbound() -> None:
    other = binding()["other_discovered_funnels"]

    assert {item["category_id"] for item in other} == {
        0,
        2,
        5,
        8,
        11,
        12,
    }

    assert all(item["binding_state"] == "unbound" for item in other)
