from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.rop_policy_evaluation import (
    EvaluationState,
    EvaluationVerdict,
    EvidenceRef,
    PolicyEvaluation,
    evaluate_first_response_case,
)
from app.services.rop_realtime_call_bridge import (
    RealtimeExactCallBuildResult,
    build_exact_realtime_call_responses,
)
from app.services.rop_response_evidence_adapter import (
    build_first_response_case_from_sources,
)
from app.storage.rop_source_coverage_store import (
    RopSourceCoverageStore,
    SourceCoverageCheck,
)


@dataclass(frozen=True, slots=True)
class RealtimeFirstResponseEvaluation:
    evaluation: PolicyEvaluation
    exact_response_source: str
    build_blockers: tuple[str, ...]
    relevant_successful_call_ids: tuple[str, ...]
    openlines_coverage: SourceCoverageCheck | None
    call_coverage: SourceCoverageCheck | None


def _datetime(
    value: Any,
) -> datetime | None:
    if value in (
        None,
        "",
    ):
        return None

    raw = (
        str(value)
        .strip()
        .replace(
            "Z",
            "+00:00",
        )
    )

    try:
        result = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)

    return result.astimezone(UTC)


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
            WHERE type IN (
                'table',
                'view'
            )
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


def _lead_created(
    database_path: str,
    lead_id: int,
) -> (
    tuple[
        datetime,
        EvidenceRef,
    ]
    | None
):
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        source = _crm_source(objects)

        if source is None:
            return None

        row = connection.execute(
            f"""
            SELECT payload_json
            FROM {source}
            WHERE entity_type = 'lead'
              AND entity_id = ?
            LIMIT 1
            """,
            (str(lead_id),),
        ).fetchone()

        if row is None:
            return None

        try:
            payload = json.loads(row["payload_json"])
        except (
            TypeError,
            json.JSONDecodeError,
        ):
            return None

        if not isinstance(
            payload,
            dict,
        ):
            return None

        created = _datetime(payload.get("DATE_CREATE"))

        if created is None:
            return None

        return (
            created,
            EvidenceRef(
                source_type=("crm_lead"),
                source_id=str(lead_id),
                occurred_at=created,
                event_kind=("lead_created"),
            ),
        )

    finally:
        connection.close()


def _activity_ids_for_lead(
    connection: sqlite3.Connection,
    *,
    source: str,
    lead_id: int,
) -> set[str]:
    rows = connection.execute(
        f"""
        SELECT
            entity_id,
            payload_json
        FROM {source}
        WHERE entity_type = 'activity'
        """
    ).fetchall()

    result: set[str] = set()

    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (
            TypeError,
            json.JSONDecodeError,
        ):
            continue

        if not isinstance(
            payload,
            dict,
        ):
            continue

        owner_type = str(payload.get("OWNER_TYPE_ID") or "")

        owner_id = str(payload.get("OWNER_ID") or "")

        if owner_type == "1" and owner_id == str(lead_id):
            result.add(str(row["entity_id"]))

    return result


def _relevant_successful_call_ids(
    database_path: str,
    *,
    lead_id: int,
) -> tuple[str, ...]:
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        if "bitrix_call_evidence" not in objects:
            return ()

        source = _crm_source(objects)

        activity_ids: set[str] = set()

        if source is not None:
            activity_ids = _activity_ids_for_lead(
                connection,
                source=source,
                lead_id=lead_id,
            )

        rows = connection.execute(
            """
            SELECT
                call_id,
                crm_activity_id,
                crm_entity_type,
                crm_entity_id
            FROM bitrix_call_evidence
            WHERE event_name =
                  'ONVOXIMPLANTCALLEND'
              AND call_failed_code = '200'
            """
        ).fetchall()

        call_ids: set[str] = set()

        for row in rows:
            call_id = str(row["call_id"] or "").strip()

            if not call_id:
                continue

            entity_type = str(row["crm_entity_type"] or "").strip().upper()

            entity_id = str(row["crm_entity_id"] or "").strip()

            activity_id = str(row["crm_activity_id"] or "").strip()

            direct = entity_type == "LEAD" and entity_id == str(lead_id)

            via_activity = activity_id in activity_ids

            if direct or via_activity:
                call_ids.add(call_id)

        return tuple(sorted(call_ids))

    finally:
        connection.close()


def _blocked(
    *,
    lead_id: int,
    reasons: tuple[str, ...],
    evidence: tuple[EvidenceRef, ...] = (),
    details: dict[
        str,
        Any,
    ]
    | None = None,
) -> PolicyEvaluation:
    return PolicyEvaluation(
        rule_key=("first_response_sla"),
        state=(EvaluationState.BLOCKED),
        verdict=(EvaluationVerdict.BLOCKED),
        entity_type="lead",
        entity_id=lead_id,
        reasons=reasons,
        evidence=evidence,
        details=(details or {}),
    )


async def evaluate_realtime_first_response(
    database_path: str,
    *,
    lead_id: int,
    as_of: datetime,
) -> RealtimeFirstResponseEvaluation:
    if lead_id <= 0:
        evaluation = _blocked(
            lead_id=lead_id,
            reasons=("lead_id_invalid",),
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=("lead_id_invalid",),
            relevant_successful_call_ids=(),
            openlines_coverage=None,
            call_coverage=None,
        )

    if as_of.tzinfo is None:
        evaluation = _blocked(
            lead_id=lead_id,
            reasons=("as_of_timezone_missing",),
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=("as_of_timezone_missing",),
            relevant_successful_call_ids=(),
            openlines_coverage=None,
            call_coverage=None,
        )

    observed_at = as_of.astimezone(UTC)

    lead = _lead_created(
        database_path,
        lead_id,
    )

    if lead is None:
        evaluation = _blocked(
            lead_id=lead_id,
            reasons=("lead_creation_evidence_missing",),
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=("lead_creation_evidence_missing",),
            relevant_successful_call_ids=(),
            openlines_coverage=None,
            call_coverage=None,
        )

    (
        lead_created_at,
        lead_evidence,
    ) = lead

    if observed_at < lead_created_at:
        evaluation = _blocked(
            lead_id=lead_id,
            reasons=("as_of_before_lead_creation",),
            evidence=(lead_evidence,),
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=("as_of_before_lead_creation",),
            relevant_successful_call_ids=(),
            openlines_coverage=None,
            call_coverage=None,
        )

    coverage_store = RopSourceCoverageStore(database_path)

    openlines = await coverage_store.check_window(
        source_key="openlines",
        window_start=lead_created_at,
        window_end=observed_at,
    )

    calls = await coverage_store.check_window(
        source_key=("voximplant_realtime"),
        window_start=lead_created_at,
        window_end=observed_at,
    )

    coverage_blockers = openlines.blockers + calls.blockers

    if coverage_blockers:
        evaluation = _blocked(
            lead_id=lead_id,
            reasons=(coverage_blockers),
            evidence=(lead_evidence,),
            details={
                "openlines_complete": openlines.complete,
                "call_complete": calls.complete,
            },
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=(coverage_blockers),
            relevant_successful_call_ids=(),
            openlines_coverage=openlines,
            call_coverage=calls,
        )

    call_build: RealtimeExactCallBuildResult = build_exact_realtime_call_responses(database_path)

    structural_blockers = tuple(
        blocker
        for blocker in call_build.blockers
        if blocker
        in {
            "realtime_call_database_missing",
            "realtime_call_evidence_tables_missing",
        }
    )

    if structural_blockers:
        evaluation = _blocked(
            lead_id=lead_id,
            reasons=(structural_blockers),
            evidence=(lead_evidence,),
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=(structural_blockers),
            relevant_successful_call_ids=(),
            openlines_coverage=openlines,
            call_coverage=calls,
        )

    relevant_success = _relevant_successful_call_ids(
        database_path,
        lead_id=lead_id,
    )

    exact_call_ids = {item.call_id for item in call_build.exact_calls}

    unresolved = tuple(call_id for call_id in relevant_success if call_id not in exact_call_ids)

    if unresolved:
        reasons = tuple(
            ("relevant_successful_call_missing_valid_exact_evidence:" + call_id)
            for call_id in unresolved
        )

        evaluation = _blocked(
            lead_id=lead_id,
            reasons=reasons,
            evidence=(lead_evidence,),
            details={
                "relevant_successful_call_ids": list(relevant_success),
            },
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=reasons,
            relevant_successful_call_ids=(relevant_success),
            openlines_coverage=openlines,
            call_coverage=calls,
        )

    build = build_first_response_case_from_sources(
        database_path,
        lead_id=lead_id,
        exact_calls=(call_build.exact_calls),
        successful_call_facts=(),
        openlines_source_complete=True,
        call_source_complete=True,
        as_of=observed_at,
    )

    if build.case is None:
        reasons = build.blockers or ("first_response_case_build_failed",)

        evaluation = _blocked(
            lead_id=lead_id,
            reasons=reasons,
            evidence=(lead_evidence,),
        )

        return RealtimeFirstResponseEvaluation(
            evaluation=evaluation,
            exact_response_source="",
            build_blockers=reasons,
            relevant_successful_call_ids=(relevant_success),
            openlines_coverage=openlines,
            call_coverage=calls,
        )

    evaluation = evaluate_first_response_case(build.case)

    return RealtimeFirstResponseEvaluation(
        evaluation=evaluation,
        exact_response_source=(build.exact_response_source),
        build_blockers=(),
        relevant_successful_call_ids=(relevant_success),
        openlines_coverage=openlines,
        call_coverage=calls,
    )
