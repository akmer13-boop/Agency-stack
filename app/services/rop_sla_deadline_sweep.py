from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.rop_policy_scope import (
    resolve_policy_scope,
)
from app.services.rop_realtime_stage_sla import (
    evaluate_realtime_deal_stage_sla,
)
from app.storage.rop_sla_deadline_sweep_store import (
    RopSlaDeadlineSweepStore,
)


@dataclass(frozen=True, slots=True)
class DeadlineSweepResult:
    source_evaluation_id: int
    entity_type: str
    entity_id: int
    stage_id: str
    outcome: str
    state: str
    verdict: str
    attempts: int
    result_code: str


async def process_next_sla_deadline(
    database_path: str,
    *,
    as_of: datetime | None = None,
    max_attempts: int = 3,
) -> DeadlineSweepResult | None:
    observed = as_of if as_of is not None else datetime.now(UTC)

    if observed.tzinfo is None:
        raise ValueError("as_of_timezone_missing")

    observed = observed.astimezone(UTC)

    store = RopSlaDeadlineSweepStore(database_path)

    candidate = await store.claim_due(
        as_of=observed,
        max_attempts=max_attempts,
    )

    if candidate is None:
        return None

    try:
        if candidate.entity_type != "deal":
            raise ValueError("unsupported_deadline_entity_type")

        scope = resolve_policy_scope(
            database_path,
            entity_type="deal",
            entity_id=candidate.entity_id,
        )

        if not scope.eligible or scope.profile_key != candidate.policy_profile:
            raise ValueError("deadline_policy_scope_changed")

        result = await evaluate_realtime_deal_stage_sla(
            database_path,
            deal_id=candidate.entity_id,
            as_of=observed,
        )

        evaluation = result.evaluation

        await store.complete(
            candidate,
            as_of=observed,
            evaluation=evaluation,
            result_code=("deadline_recheck_evaluated"),
        )

        return DeadlineSweepResult(
            source_evaluation_id=(candidate.source_evaluation_id),
            entity_type=candidate.entity_type,
            entity_id=candidate.entity_id,
            stage_id=candidate.stage_id,
            outcome="completed",
            state=evaluation.state.value,
            verdict=evaluation.verdict.value,
            attempts=candidate.attempts,
            result_code=("deadline_recheck_evaluated"),
        )

    except Exception as exc:
        error_code = (type(exc).__name__ or "SLA_DEADLINE_SWEEP_ERROR")[:120]

        await store.fail(
            candidate,
            error_code=error_code,
        )

        return DeadlineSweepResult(
            source_evaluation_id=(candidate.source_evaluation_id),
            entity_type=candidate.entity_type,
            entity_id=candidate.entity_id,
            stage_id=candidate.stage_id,
            outcome="failed",
            state="",
            verdict="",
            attempts=candidate.attempts,
            result_code=error_code,
        )


async def process_sla_deadline_batch(
    database_path: str,
    *,
    limit: int = 100,
    as_of: datetime | None = None,
    max_attempts: int = 3,
) -> list[DeadlineSweepResult]:
    if limit < 1:
        raise ValueError("limit must be positive")

    results: list[DeadlineSweepResult] = []

    for _ in range(limit):
        result = await process_next_sla_deadline(
            database_path,
            as_of=as_of,
            max_attempts=max_attempts,
        )

        if result is None:
            break

        results.append(result)

    return results
