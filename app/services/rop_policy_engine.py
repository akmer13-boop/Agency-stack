from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_POLICY_PATH = "config/rop-business-policies.json"
DEFAULT_BINDING_PATH = "config/rop-bitrix-bindings.json"


class RuleState(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class Verdict(StrEnum):
    OK = "ok"
    ATTENTION = "attention"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class PolicyContract:
    policies: dict[str, dict[str, Any]]
    binding: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    policy_key: str
    state: RuleState
    verdict: Verdict
    threshold_seconds: int | None = None
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def _read_json(path: str) -> dict[str, Any]:
    source = Path(path)

    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"policy_engine_file_missing:{path}") from exc
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(f"policy_engine_file_invalid:{path}") from exc

    if not isinstance(value, dict):
        raise ValueError(f"policy_engine_root_invalid:{path}")

    return value


def load_policy_contract(
    policy_path: str = DEFAULT_POLICY_PATH,
    binding_path: str = DEFAULT_BINDING_PATH,
) -> PolicyContract:
    policy_doc = _read_json(policy_path)
    binding_doc = _read_json(binding_path)

    if policy_doc.get("schema_version") != 1:
        raise ValueError("policy_schema_unsupported")

    if binding_doc.get("schema_version") != 1:
        raise ValueError("binding_schema_unsupported")

    policies = policy_doc.get("policies")
    funnel = binding_doc.get("business_policy_funnel")

    if not isinstance(policies, dict):
        raise ValueError("policies_missing")

    if not isinstance(funnel, dict):
        raise ValueError("business_policy_funnel_missing")

    return PolicyContract(
        policies=policies,
        binding=binding_doc,
    )


def _policy(
    contract: PolicyContract,
    key: str,
) -> dict[str, Any]:
    value = contract.policies.get(key)

    if not isinstance(value, dict):
        raise ValueError(f"policy_missing:{key}")

    return value


def _params(
    contract: PolicyContract,
    key: str,
) -> dict[str, Any]:
    value = _policy(contract, key).get("parameters")

    if not isinstance(value, dict):
        raise ValueError(f"policy_parameters_invalid:{key}")

    return value


def _approved(
    contract: PolicyContract,
    key: str,
) -> bool:
    return (
        _policy(
            contract,
            key,
        ).get("approval_status")
        == "approved"
    )


def _stage_bindings(
    contract: PolicyContract,
) -> dict[str, dict[str, Any]]:
    funnel = contract.binding["business_policy_funnel"]

    value = funnel.get("stage_sla")

    if not isinstance(value, dict):
        raise ValueError("stage_sla_binding_missing")

    return value


def _find_stage(
    contract: PolicyContract,
    stage_id: str,
) -> tuple[str, dict[str, Any]] | None:
    for key, value in _stage_bindings(contract).items():
        if value.get("status_id") == stage_id:
            return key, value

    return None


def first_response_readiness(
    contract: PolicyContract,
) -> PolicyDecision:
    key = "first_response_sla"

    if not _approved(contract, key):
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            reasons=("business_policy_not_approved",),
        )

    params = _params(contract, key)

    blocked = tuple(params.get("blocked_fields", ()))

    if blocked:
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            threshold_seconds=params.get("threshold_seconds"),
            reasons=tuple(f"missing_business_calendar:{item}" for item in blocked),
            details={
                "timer_start": params.get("timer_start"),
                "clock": params.get("clock"),
                "reassignment": params.get("reassignment"),
            },
        )

    return PolicyDecision(
        policy_key=key,
        state=RuleState.READY,
        verdict=Verdict.OK,
        threshold_seconds=params.get("threshold_seconds"),
    )


def stage_stale_readiness(
    contract: PolicyContract,
    stage_id: str,
) -> PolicyDecision:
    key = "stale_deal"

    if not _approved(contract, key):
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            reasons=("business_policy_not_approved",),
        )

    found = _find_stage(
        contract,
        stage_id,
    )

    if found is None:
        return PolicyDecision(
            policy_key=key,
            state=RuleState.NOT_APPLICABLE,
            verdict=Verdict.NOT_APPLICABLE,
            reasons=("stage_not_bound_to_b2c_policy",),
            details={
                "stage_id": stage_id,
            },
        )

    binding_key, binding = found
    params = _params(contract, key)

    reasons: list[str] = []

    if binding.get("needs_business_confirmation"):
        reasons.append("stage_binding_unconfirmed:" + binding_key)

    blocked_fields = set(params.get("blocked_fields", ()))

    if "general_vs_stage_threshold_precedence" in blocked_fields:
        reasons.append("stale_threshold_precedence_not_defined")

    questionnaire_name = binding.get("questionnaire_name")

    stage_thresholds = params.get(
        "stage_thresholds",
        {},
    )

    stage_rule = (
        stage_thresholds.get(questionnaire_name)
        if isinstance(
            stage_thresholds,
            dict,
        )
        else None
    )

    if not isinstance(stage_rule, dict):
        stage_rule = {}
        reasons.append("stage_threshold_not_found")

    threshold = stage_rule.get("threshold_seconds")

    threshold_mode = stage_rule.get("threshold_mode")

    if (
        threshold_mode == "return_to_client_date"
        and "potential_client_return_date_field_id" in blocked_fields
    ):
        reasons.append("return_to_client_field_not_bound")

    details = {
        "stage_id": stage_id,
        "binding_key": binding_key,
        "bitrix_name": binding.get("bitrix_name"),
        "questionnaire_name": questionnaire_name,
        "general_threshold_seconds": params.get("general_inactivity_threshold_seconds"),
        "stage_threshold_seconds": threshold,
        "threshold_mode": threshold_mode,
        "qualifying_activity": params.get(
            "qualifying_activity",
            [],
        ),
    }

    if reasons:
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            threshold_seconds=(
                threshold
                if isinstance(
                    threshold,
                    int,
                )
                else None
            ),
            reasons=tuple(reasons),
            details=details,
        )

    return PolicyDecision(
        policy_key=key,
        state=RuleState.READY,
        verdict=Verdict.OK,
        threshold_seconds=(
            threshold
            if isinstance(
                threshold,
                int,
            )
            else None
        ),
        details=details,
    )


def proposal_readiness(
    contract: PolicyContract,
) -> PolicyDecision:
    key = "proposal_stale"

    if not _approved(contract, key):
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            reasons=("business_policy_not_approved",),
        )

    proposal = _stage_bindings(contract).get("proposal_sent")

    if not isinstance(proposal, dict):
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            reasons=("proposal_stage_binding_missing",),
        )

    threshold = _params(
        contract,
        key,
    ).get("attention_after_no_client_response_seconds")

    if proposal.get("needs_business_confirmation"):
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            threshold_seconds=threshold,
            reasons=("proposal_stage_binding_unconfirmed",),
            details={
                "status_id": proposal.get("status_id"),
                "bitrix_name": proposal.get("bitrix_name"),
            },
        )

    return PolicyDecision(
        policy_key=key,
        state=RuleState.READY,
        verdict=Verdict.OK,
        threshold_seconds=threshold,
    )


def conversion_readiness(
    contract: PolicyContract,
) -> PolicyDecision:
    key = "business_conversion"

    if not _approved(contract, key):
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            reasons=("business_policy_not_approved",),
        )

    funnel = contract.binding["business_policy_funnel"]

    sequence = funnel.get(
        "conversion_sequence",
        [],
    )

    if not isinstance(sequence, list):
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            reasons=("conversion_sequence_invalid",),
        )

    unconfirmed = {
        item.get("status_id")
        for item in _stage_bindings(contract).values()
        if item.get("needs_business_confirmation")
    }

    blockers = [step.get("status_id") for step in sequence if step.get("status_id") in unconfirmed]

    if blockers:
        return PolicyDecision(
            policy_key=key,
            state=RuleState.BLOCKED,
            verdict=Verdict.BLOCKED,
            reasons=tuple("conversion_stage_binding_unconfirmed:" + str(item) for item in blockers),
            details={
                "sequence": sequence,
            },
        )

    return PolicyDecision(
        policy_key=key,
        state=RuleState.READY,
        verdict=Verdict.OK,
        details={
            "sequence": sequence,
        },
    )


def classify_sale_stage(
    contract: PolicyContract,
    stage_id: str,
) -> PolicyDecision:
    funnel = contract.binding["business_policy_funnel"]

    groups = (
        (
            "success",
            funnel.get(
                "successful_sale_stages",
                {},
            ),
        ),
        (
            "lost",
            funnel.get(
                "lost_sale_stages",
                {},
            ),
        ),
    )

    for classification, group in groups:
        for binding_key, value in group.items():
            if value.get("status_id") == stage_id:
                return PolicyDecision(
                    policy_key=("sale_stage_classification"),
                    state=RuleState.READY,
                    verdict=Verdict.OK,
                    details={
                        "classification": classification,
                        "binding_key": binding_key,
                        "status_id": stage_id,
                        "name": value.get("name"),
                    },
                )

    return PolicyDecision(
        policy_key=("sale_stage_classification"),
        state=RuleState.NOT_APPLICABLE,
        verdict=Verdict.NOT_APPLICABLE,
        reasons=("stage_not_in_success_or_loss_binding",),
        details={
            "status_id": stage_id,
        },
    )


def qualifying_activity(
    contract: PolicyContract,
) -> tuple[str, ...]:
    values = _params(
        contract,
        "stale_deal",
    ).get(
        "qualifying_activity",
        [],
    )

    return tuple(str(value) for value in values)


def policy_engine_readiness(
    contract: PolicyContract,
) -> dict[str, PolicyDecision]:
    return {
        "first_response": first_response_readiness(contract),
        "stale_new": stage_stale_readiness(
            contract,
            "C7:NEW",
        ),
        "stale_needs_discovery": stage_stale_readiness(
            contract,
            "C7:PREPARATION",
        ),
        "proposal": proposal_readiness(contract),
        "conversion": conversion_readiness(contract),
        "won_example": classify_sale_stage(
            contract,
            "C7:WON",
        ),
        "lost_example": classify_sale_stage(
            contract,
            "C7:LOSE",
        ),
    }
