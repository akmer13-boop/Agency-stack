from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.rop_policy_scope import (
    PolicyScopeDecision,
    resolve_policy_scope,
)
from app.services.rop_realtime_first_response import (
    evaluate_realtime_first_response,
)
from app.services.rop_realtime_stage_sla import (
    evaluate_realtime_deal_stage_sla,
)
from app.storage.rop_sla_runtime_store import (
    RopSlaRuntimeStore,
    SlaDispatchEvent,
)

_DELETE_EVENTS = frozenset(
    {
        "ONCRMLEADDELETE",
        "ONCRMDEALDELETE",
        "ONCRMACTIVITYDELETE",
    }
)


@dataclass(frozen=True, slots=True)
class SlaTarget:
    entity_type: str
    entity_id: int


@dataclass(frozen=True, slots=True)
class SlaDispatchResult:
    inbox_id: int
    event_name: str
    outcome: str
    targets_observed: int
    evaluations_written: int
    notes: tuple[str, ...]
    attempts: int


def _connect(
    database_path: str,
) -> sqlite3.Connection:
    path = Path(database_path).resolve()

    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")

    return connection


def _objects(
    connection: sqlite3.Connection,
) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
            """
        )
    }


def _crm_source(
    objects: set[str],
) -> str | None:
    if "crm_active_entities" in objects:
        return "crm_active_entities"

    if "crm_raw_entities" in objects:
        return "crm_raw_entities"

    return None


def _payload(
    value: Any,
) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None

    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return None

    return result if isinstance(result, dict) else None


def _positive_id(
    value: Any,
) -> int | None:
    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ):
        return None

    return result if result > 0 else None


def _activity_owner(
    database_path: str,
    activity_id: str,
) -> SlaTarget | None:
    if not activity_id:
        return None

    connection = _connect(database_path)

    try:
        source = _crm_source(_objects(connection))

        if source is None:
            return None

        row = connection.execute(
            f"""
            SELECT payload_json
            FROM {source}
            WHERE entity_type = 'activity'
              AND entity_id = ?
            LIMIT 1
            """,
            (activity_id,),
        ).fetchone()

        if row is None:
            return None

        item = _payload(row["payload_json"])

        if item is None:
            return None

        owner_type = str(item.get("OWNER_TYPE_ID") or "").strip()

        owner_id = _positive_id(item.get("OWNER_ID"))

        if owner_id is None:
            return None

        if owner_type == "1":
            return SlaTarget(
                entity_type="lead",
                entity_id=owner_id,
            )

        if owner_type == "2":
            return SlaTarget(
                entity_type="deal",
                entity_id=owner_id,
            )

        return None

    finally:
        connection.close()


def _deal_linked_lead(
    database_path: str,
    deal_id: int,
) -> int | None:
    connection = _connect(database_path)

    try:
        source = _crm_source(_objects(connection))

        if source is None:
            return None

        row = connection.execute(
            f"""
            SELECT payload_json
            FROM {source}
            WHERE entity_type = 'deal'
              AND entity_id = ?
            LIMIT 1
            """,
            (str(deal_id),),
        ).fetchone()

        if row is None:
            return None

        item = _payload(row["payload_json"])

        if item is None:
            return None

        return _positive_id(item.get("LEAD_ID"))

    finally:
        connection.close()


def _call_targets(
    database_path: str,
    event_key: str,
) -> tuple[SlaTarget, ...]:
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        if "bitrix_call_evidence" not in objects:
            return ()

        row = connection.execute(
            """
            SELECT
                crm_activity_id,
                crm_entity_type,
                crm_entity_id
            FROM bitrix_call_evidence
            WHERE event_key = ?
            LIMIT 1
            """,
            (event_key,),
        ).fetchone()

    finally:
        connection.close()

    if row is None:
        return ()

    targets: set[tuple[str, int]] = set()

    entity_type = str(row["crm_entity_type"] or "").strip().upper()

    entity_id = _positive_id(row["crm_entity_id"])

    if entity_id is not None:
        if entity_type == "LEAD":
            targets.add(("lead", entity_id))

        elif entity_type == "DEAL":
            targets.add(("deal", entity_id))

    activity_id = str(row["crm_activity_id"] or "").strip()

    owner = _activity_owner(
        database_path,
        activity_id,
    )

    if owner is not None:
        targets.add(
            (
                owner.entity_type,
                owner.entity_id,
            )
        )

    return tuple(
        SlaTarget(
            entity_type=entity_type_value,
            entity_id=entity_id_value,
        )
        for (
            entity_type_value,
            entity_id_value,
        ) in sorted(targets)
    )


def resolve_sla_targets(
    database_path: str,
    event: SlaDispatchEvent,
) -> tuple[
    tuple[SlaTarget, ...],
    tuple[str, ...],
]:
    if event.event_name in _DELETE_EVENTS:
        return (
            (),
            ("delete_event_no_sla_evaluation",),
        )

    targets: set[tuple[str, int]] = set()

    if event.entity_type in {
        "lead",
        "deal",
    }:
        entity_id = _positive_id(event.entity_id)

        if entity_id is not None:
            targets.add(
                (
                    event.entity_type,
                    entity_id,
                )
            )

            if event.entity_type == "deal":
                linked_lead_id = _deal_linked_lead(
                    database_path,
                    entity_id,
                )

                if linked_lead_id is not None:
                    targets.add(
                        (
                            "lead",
                            linked_lead_id,
                        )
                    )

    elif event.entity_type == "activity":
        owner = _activity_owner(
            database_path,
            event.entity_id,
        )

        if owner is not None:
            targets.add(
                (
                    owner.entity_type,
                    owner.entity_id,
                )
            )

    elif event.entity_type == "call":
        for target in _call_targets(
            database_path,
            event.event_key,
        ):
            targets.add(
                (
                    target.entity_type,
                    target.entity_id,
                )
            )

    result = tuple(
        SlaTarget(
            entity_type=entity_type,
            entity_id=entity_id,
        )
        for (
            entity_type,
            entity_id,
        ) in sorted(targets)
    )

    if result:
        return (
            result,
            (),
        )

    return (
        (),
        ("sla_target_not_resolved",),
    )


async def _evaluate_target(
    database_path: str,
    *,
    target: SlaTarget,
    scope: PolicyScopeDecision,
    as_of: datetime,
):
    if target.entity_type == "lead":
        result = await evaluate_realtime_first_response(
            database_path,
            lead_id=target.entity_id,
            as_of=as_of,
        )

        return result.evaluation

    if target.entity_type == "deal":
        result = await evaluate_realtime_deal_stage_sla(
            database_path,
            deal_id=target.entity_id,
            as_of=as_of,
        )

        return result.evaluation

    raise ValueError("unsupported_sla_target_type:" + target.entity_type)


async def process_next_realtime_sla(
    database_path: str,
    *,
    as_of: datetime | None = None,
    max_attempts: int = 3,
) -> SlaDispatchResult | None:
    observed_at = as_of or datetime.now(UTC)

    if observed_at.tzinfo is None:
        raise ValueError("as_of_timezone_missing")

    observed_at = observed_at.astimezone(UTC)

    store = RopSlaRuntimeStore(database_path)

    event = await store.claim_next(max_attempts=max_attempts)

    if event is None:
        return None

    try:
        trigger_at = datetime.fromtimestamp(
            event.event_ts,
            tz=UTC,
        )

        if observed_at < trigger_at:
            raise ValueError("as_of_before_trigger_event")

        (
            targets,
            target_notes,
        ) = resolve_sla_targets(
            database_path,
            event,
        )

        notes = list(target_notes)
        evaluations_written = 0

        for target in targets:
            scope = resolve_policy_scope(
                database_path,
                entity_type=target.entity_type,
                entity_id=target.entity_id,
            )

            if not scope.eligible:
                notes.append(scope.reason)
                continue

            evaluation = await _evaluate_target(
                database_path,
                target=target,
                scope=scope,
                as_of=observed_at,
            )

            await store.record_evaluation(
                event=event,
                policy_profile=(scope.profile_key),
                evaluated_as_of=(observed_at.isoformat()),
                evaluation=evaluation,
            )

            evaluations_written += 1

        if evaluations_written:
            result_code = "sla_evaluations_written"

        elif targets:
            result_code = "sla_scope_skipped"

        else:
            result_code = "no_sla_target"

        await store.complete(
            event.inbox_id,
            result_code=result_code,
            targets_observed=len(targets),
            evaluations_written=(evaluations_written),
            notes=tuple(notes),
        )

        return SlaDispatchResult(
            inbox_id=event.inbox_id,
            event_name=event.event_name,
            outcome="completed",
            targets_observed=len(targets),
            evaluations_written=(evaluations_written),
            notes=tuple(sorted(set(notes))),
            attempts=event.attempts,
        )

    except Exception as exc:
        error_code = (type(exc).__name__ or "SLA_ORCHESTRATOR_ERROR")[:120]

        await store.fail(
            event.inbox_id,
            error_code=error_code,
        )

        return SlaDispatchResult(
            inbox_id=event.inbox_id,
            event_name=event.event_name,
            outcome="failed",
            targets_observed=0,
            evaluations_written=0,
            notes=(error_code,),
            attempts=event.attempts,
        )


async def process_realtime_sla_batch(
    database_path: str,
    *,
    limit: int = 20,
    as_of: datetime | None = None,
    max_attempts: int = 3,
) -> list[SlaDispatchResult]:
    if limit < 1:
        raise ValueError("limit must be positive")

    results: list[SlaDispatchResult] = []

    for _ in range(limit):
        result = await process_next_realtime_sla(
            database_path,
            as_of=as_of,
            max_attempts=max_attempts,
        )

        if result is None:
            break

        results.append(result)

    return results
