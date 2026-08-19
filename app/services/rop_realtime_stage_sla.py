from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.rop_policy_engine import (
    RuleState,
    load_policy_contract,
    stage_stale_readiness,
)
from app.services.rop_policy_evaluation import (
    EvaluationState,
    EvaluationVerdict,
    EvidenceRef,
    PolicyEvaluation,
    StageTimerCase,
    evaluate_stage_timer_case,
)
from app.services.rop_realtime_call_bridge import (
    build_exact_realtime_call_responses,
)
from app.storage.rop_source_coverage_store import (
    RopSourceCoverageStore,
    SourceCoverageCheck,
)

_CALL_KIND = {
    "1": "outbound_call",
    "2": "inbound_call",
}


@dataclass(frozen=True, slots=True)
class _StageContext:
    deal_id: int
    stage_id: str
    stage_entered_at: datetime
    stage_evidence: EvidenceRef


@dataclass(frozen=True, slots=True)
class _ActivityCandidate:
    kind: str
    occurred_at: datetime
    evidence: EvidenceRef
    source_name: str


@dataclass(frozen=True, slots=True)
class RealtimeDealStageEvaluation:
    evaluation: PolicyEvaluation
    stage_id: str
    stage_entry_source: str
    last_qualifying_activity_kind: str
    last_qualifying_activity_source: str
    relevant_successful_call_ids: tuple[str, ...]
    build_blockers: tuple[str, ...]
    crm_coverage: SourceCoverageCheck | None
    openlines_coverage: SourceCoverageCheck | None
    call_coverage: SourceCoverageCheck | None


def _dt(
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
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    parsed = parsed.astimezone(UTC)

    if parsed.year >= 2099:
        return None

    return parsed


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


def _payload(
    value: Any,
) -> dict[str, Any] | None:
    if not isinstance(
        value,
        str,
    ):
        return None

    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return None

    return (
        result
        if isinstance(
            result,
            dict,
        )
        else None
    )


def _int_or_none(
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


def _completed(
    value: Any,
) -> bool:
    if isinstance(
        value,
        bool,
    ):
        return value

    return str(value).strip().upper() in {
        "Y",
        "YES",
        "TRUE",
        "1",
    }


def _activity_time(
    payload: dict[str, Any],
) -> datetime | None:
    for key in (
        "END_TIME",
        "START_TIME",
        "LAST_UPDATED",
        "CREATED",
        "DEADLINE",
    ):
        value = _dt(payload.get(key))

        if value is not None:
            return value

    return None


def _blocked(
    *,
    deal_id: int,
    stage_id: str,
    reasons: tuple[str, ...],
    evidence: tuple[EvidenceRef, ...] = (),
    details: dict[
        str,
        Any,
    ]
    | None = None,
) -> PolicyEvaluation:
    return PolicyEvaluation(
        rule_key="stale_deal",
        state=EvaluationState.BLOCKED,
        verdict=EvaluationVerdict.BLOCKED,
        entity_type="deal",
        entity_id=deal_id,
        stage_id=stage_id,
        reasons=reasons,
        evidence=evidence,
        details=(details or {}),
    )


def _empty_result(
    *,
    evaluation: PolicyEvaluation,
    stage_id: str = "",
    stage_entry_source: str = "",
    blockers: tuple[str, ...] = (),
    crm_coverage: SourceCoverageCheck | None = None,
    openlines_coverage: SourceCoverageCheck | None = None,
    call_coverage: SourceCoverageCheck | None = None,
) -> RealtimeDealStageEvaluation:
    return RealtimeDealStageEvaluation(
        evaluation=evaluation,
        stage_id=stage_id,
        stage_entry_source=stage_entry_source,
        last_qualifying_activity_kind="",
        last_qualifying_activity_source="",
        relevant_successful_call_ids=(),
        build_blockers=blockers,
        crm_coverage=crm_coverage,
        openlines_coverage=openlines_coverage,
        call_coverage=call_coverage,
    )


def _load_stage_context(
    database_path: str,
    *,
    deal_id: int,
    as_of: datetime,
) -> tuple[
    _StageContext | None,
    str,
]:
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        source = _crm_source(objects)

        if source is None:
            return (
                None,
                "crm_source_missing",
            )

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
            return (
                None,
                "deal_snapshot_missing",
            )

        deal = _payload(row["payload_json"])

        if deal is None:
            return (
                None,
                "deal_snapshot_invalid",
            )

        stage_id = str(deal.get("STAGE_ID") or "").strip()

        if not stage_id:
            return (
                None,
                "deal_stage_missing",
            )

        history_rows = connection.execute(
            f"""
            SELECT
                entity_id,
                payload_json
            FROM {source}
            WHERE entity_type =
                  'deal_stage_history'
            """
        ).fetchall()

        history: list[
            tuple[
                datetime,
                str,
            ]
        ] = []

        for history_row in history_rows:
            item = _payload(history_row["payload_json"])

            if item is None:
                continue

            if str(item.get("OWNER_ID") or "") != str(deal_id):
                continue

            if str(item.get("STAGE_ID") or "") != stage_id:
                continue

            occurred_at = _dt(item.get("CREATED_TIME"))

            if occurred_at is None or occurred_at > as_of:
                continue

            history.append(
                (
                    occurred_at,
                    str(history_row["entity_id"]),
                )
            )

        if history:
            (
                entered_at,
                history_id,
            ) = max(
                history,
                key=lambda item: (
                    item[0],
                    item[1],
                ),
            )

            return (
                _StageContext(
                    deal_id=deal_id,
                    stage_id=stage_id,
                    stage_entered_at=(entered_at),
                    stage_evidence=EvidenceRef(
                        source_type=("crm_deal_stage_history"),
                        source_id=history_id,
                        occurred_at=(entered_at),
                        event_kind=("deal_stage_entered"),
                    ),
                ),
                ("crm_deal_stage_history"),
            )

        moved_at = _dt(deal.get("MOVED_TIME"))

        if moved_at is not None and moved_at <= as_of:
            return (
                _StageContext(
                    deal_id=deal_id,
                    stage_id=stage_id,
                    stage_entered_at=(moved_at),
                    stage_evidence=EvidenceRef(
                        source_type=("crm_deal"),
                        source_id=str(deal_id),
                        occurred_at=(moved_at),
                        event_kind=("deal_stage_entered_moved_time"),
                    ),
                ),
                "crm_deal_moved_time",
            )

        return (
            None,
            "stage_entry_evidence_missing",
        )

    finally:
        connection.close()


def _activity_ids_for_deal(
    connection: sqlite3.Connection,
    *,
    source: str,
    deal_id: int,
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
        item = _payload(row["payload_json"])

        if item is None:
            continue

        if str(item.get("OWNER_TYPE_ID") or "") == "2" and str(item.get("OWNER_ID") or "") == str(
            deal_id
        ):
            result.add(str(row["entity_id"]))

    return result


def _message_candidates(
    database_path: str,
    *,
    deal_id: int,
    start: datetime,
    end: datetime,
) -> tuple[
    list[_ActivityCandidate],
    tuple[str, ...],
]:
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        required = {
            "openlines_crm_links",
            "openlines_messages",
        }

        if not required.issubset(objects):
            return (
                [],
                ("openlines_tables_missing",),
            )

        rows = connection.execute(
            """
            SELECT
                msg.message_id,
                msg.sent_at,
                msg.sender_directory_user_id
            FROM openlines_crm_links
                AS link
            JOIN openlines_messages
                AS msg
              ON msg.chat_id = link.chat_id
            WHERE link.entity_type = 'deal'
              AND link.entity_id = ?
              AND msg.sender_role =
                  'manager'
              AND
                  msg.sender_directory_user_id
                  IS NOT NULL
              AND msg.sent_at IS NOT NULL
            """,
            (str(deal_id),),
        ).fetchall()

    finally:
        connection.close()

    candidates: list[_ActivityCandidate] = []

    for row in rows:
        occurred_at = _dt(row["sent_at"])

        if occurred_at is None or occurred_at < start or occurred_at > end:
            continue

        actor_id = _int_or_none(row["sender_directory_user_id"])

        if actor_id is None:
            continue

        candidates.append(
            _ActivityCandidate(
                kind=("message_to_client"),
                occurred_at=occurred_at,
                evidence=EvidenceRef(
                    source_type=("openlines_message"),
                    source_id=str(row["message_id"]),
                    occurred_at=(occurred_at),
                    event_kind=("message_to_client"),
                    actor_kind=("directory_user"),
                    actor_id=actor_id,
                ),
                source_name=("openlines_message"),
            )
        )

    return (
        candidates,
        (),
    )


def _email_candidates(
    database_path: str,
    *,
    deal_id: int,
    start: datetime,
    end: datetime,
) -> list[_ActivityCandidate]:
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        source = _crm_source(objects)

        if source is None:
            return []

        rows = connection.execute(
            f"""
            SELECT
                entity_id,
                payload_json
            FROM {source}
            WHERE entity_type =
                  'activity'
            """
        ).fetchall()

    finally:
        connection.close()

    result: list[_ActivityCandidate] = []

    for row in rows:
        item = _payload(row["payload_json"])

        if item is None:
            continue

        if str(item.get("OWNER_TYPE_ID") or "") != "2" or str(item.get("OWNER_ID") or "") != str(
            deal_id
        ):
            continue

        if str(item.get("TYPE_ID") or "") != "4":
            continue

        if str(item.get("DIRECTION") or "") != "2":
            continue

        if not _completed(item.get("COMPLETED")):
            continue

        occurred_at = _activity_time(item)

        if occurred_at is None or occurred_at < start or occurred_at > end:
            continue

        actor_id = _int_or_none(item.get("RESPONSIBLE_ID"))

        result.append(
            _ActivityCandidate(
                kind=("message_to_client"),
                occurred_at=(occurred_at),
                evidence=EvidenceRef(
                    source_type=("crm_activity_email"),
                    source_id=str(row["entity_id"]),
                    occurred_at=(occurred_at),
                    event_kind=("message_to_client"),
                    actor_kind=("directory_user" if actor_id is not None else ""),
                    actor_id=actor_id,
                ),
                source_name=("crm_activity_email"),
            )
        )

    return result


def _call_types(
    database_path: str,
) -> dict[
    str,
    str,
]:
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        required = {
            "bitrix_call_evidence",
            "bitrix_event_inbox",
        }

        if not required.issubset(objects):
            return {}

        rows = connection.execute(
            """
            SELECT
                evidence.call_id,
                inbox.data_json
            FROM bitrix_call_evidence
                AS evidence
            JOIN bitrix_event_inbox
                AS inbox
              ON inbox.id =
                 evidence.inbox_id
            WHERE evidence.call_id
                  IS NOT NULL
              AND evidence.call_id
                  <> ''
            """
        ).fetchall()

    finally:
        connection.close()

    observed: dict[
        str,
        set[str],
    ] = {}

    for row in rows:
        item = _payload(row["data_json"])

        if item is None:
            continue

        call_type = str(item.get("CALL_TYPE") or "")

        if call_type not in _CALL_KIND:
            continue

        call_id = str(row["call_id"])

        observed.setdefault(
            call_id,
            set(),
        ).add(call_type)

    return {
        call_id: next(iter(values))
        for (
            call_id,
            values,
        ) in observed.items()
        if len(values) == 1
    }


def _successful_calls_for_deal(
    database_path: str,
    *,
    deal_id: int,
    start: datetime,
    end: datetime,
) -> tuple[str, ...]:
    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        if "bitrix_call_evidence" not in objects:
            return ()

        source = _crm_source(objects)

        activity_ids = (
            _activity_ids_for_deal(
                connection,
                source=source,
                deal_id=deal_id,
            )
            if source is not None
            else set()
        )

        rows = connection.execute(
            """
            SELECT
                call_id,
                event_ts,
                crm_activity_id,
                crm_entity_type,
                crm_entity_id
            FROM bitrix_call_evidence
            WHERE event_name =
                  'ONVOXIMPLANTCALLEND'
              AND call_failed_code = '200'
            """
        ).fetchall()

    finally:
        connection.close()

    result: set[str] = set()

    for row in rows:
        ended_at = datetime.fromtimestamp(
            int(row["event_ts"]),
            tz=UTC,
        )

        if ended_at < start or ended_at > end:
            continue

        direct = str(row["crm_entity_type"] or "").upper() == "DEAL" and str(
            row["crm_entity_id"] or ""
        ) == str(deal_id)

        via_activity = str(row["crm_activity_id"] or "") in activity_ids

        if direct or via_activity:
            result.add(str(row["call_id"]))

    return tuple(sorted(result))


def _call_candidates(
    database_path: str,
    *,
    deal_id: int,
    start: datetime,
    end: datetime,
) -> tuple[
    list[_ActivityCandidate],
    tuple[str, ...],
    tuple[str, ...],
]:
    build = build_exact_realtime_call_responses(database_path)

    structural = tuple(
        blocker
        for blocker in build.blockers
        if blocker
        in {
            "realtime_call_database_missing",
            "realtime_call_evidence_tables_missing",
        }
    )

    if structural:
        return (
            [],
            structural,
            (),
        )

    connection = _connect(database_path)

    try:
        objects = _objects(connection)

        source = _crm_source(objects)

        activity_ids = (
            _activity_ids_for_deal(
                connection,
                source=source,
                deal_id=deal_id,
            )
            if source is not None
            else set()
        )

    finally:
        connection.close()

    types = _call_types(database_path)

    candidates: list[_ActivityCandidate] = []

    valid_exact_ids: set[str] = set()

    blockers: list[str] = []

    for exact in build.exact_calls:
        direct = exact.crm_entity_type == "DEAL" and exact.crm_entity_id == str(deal_id)

        via_activity = exact.crm_activity_id in activity_ids

        if not (direct or via_activity):
            continue

        valid_exact_ids.add(exact.call_id)

        if exact.response_at < start or exact.response_at > end:
            continue

        call_type = types.get(
            exact.call_id,
            "",
        )

        kind = _CALL_KIND.get(call_type)

        if kind is None:
            blockers.append("exact_call_direction_missing:" + exact.call_id)

            continue

        candidates.append(
            _ActivityCandidate(
                kind=kind,
                occurred_at=(exact.response_at),
                evidence=EvidenceRef(
                    source_type=("voximplant_call_start_event"),
                    source_id=(exact.call_id),
                    occurred_at=(exact.response_at),
                    event_kind=kind,
                    actor_kind=("directory_user"),
                    actor_id=(exact.manager_user_id),
                ),
                source_name=("voximplant_call_start_event"),
            )
        )

    relevant_success = _successful_calls_for_deal(
        database_path,
        deal_id=deal_id,
        start=start,
        end=end,
    )

    unresolved = tuple(call_id for call_id in relevant_success if call_id not in valid_exact_ids)

    blockers.extend(
        ("relevant_successful_call_missing_valid_exact_evidence:" + call_id)
        for call_id in unresolved
    )

    return (
        candidates,
        tuple(sorted(blockers)),
        relevant_success,
    )


async def evaluate_realtime_deal_stage_sla(
    database_path: str,
    *,
    deal_id: int,
    as_of: datetime,
) -> RealtimeDealStageEvaluation:
    if deal_id <= 0:
        evaluation = _blocked(
            deal_id=deal_id,
            stage_id="",
            reasons=("deal_id_invalid",),
        )

        return _empty_result(
            evaluation=evaluation,
            blockers=("deal_id_invalid",),
        )

    if as_of.tzinfo is None:
        evaluation = _blocked(
            deal_id=deal_id,
            stage_id="",
            reasons=("as_of_timezone_missing",),
        )

        return _empty_result(
            evaluation=evaluation,
            blockers=("as_of_timezone_missing",),
        )

    observed_at = as_of.astimezone(UTC)

    try:
        (
            context,
            entry_source,
        ) = _load_stage_context(
            database_path,
            deal_id=deal_id,
            as_of=observed_at,
        )
    except sqlite3.OperationalError:
        context = None
        entry_source = "deal_database_missing"

    if context is None:
        evaluation = _blocked(
            deal_id=deal_id,
            stage_id="",
            reasons=(entry_source,),
        )

        return _empty_result(
            evaluation=evaluation,
            blockers=(entry_source,),
        )

    if observed_at < context.stage_entered_at:
        evaluation = _blocked(
            deal_id=deal_id,
            stage_id=(context.stage_id),
            reasons=("as_of_before_stage_entry",),
            evidence=(context.stage_evidence,),
        )

        return _empty_result(
            evaluation=evaluation,
            stage_id=(context.stage_id),
            stage_entry_source=(entry_source),
            blockers=("as_of_before_stage_entry",),
        )

    contract = load_policy_contract()

    readiness = stage_stale_readiness(
        contract,
        context.stage_id,
    )

    preliminary = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=deal_id,
            stage_id=(context.stage_id),
            stage_entered_at=(context.stage_entered_at),
            as_of=observed_at,
            stage_entry_evidence=(context.stage_evidence),
        )
    )

    if readiness.state is not RuleState.READY:
        return _empty_result(
            evaluation=preliminary,
            stage_id=(context.stage_id),
            stage_entry_source=(entry_source),
            blockers=(preliminary.reasons),
        )

    coverage = RopSourceCoverageStore(database_path)

    crm_coverage = await coverage.check_window(
        source_key=("crm_realtime"),
        window_start=(context.stage_entered_at),
        window_end=(observed_at),
    )

    openlines_coverage = await coverage.check_window(
        source_key="openlines",
        window_start=(context.stage_entered_at),
        window_end=(observed_at),
    )

    call_coverage = await coverage.check_window(
        source_key=("voximplant_realtime"),
        window_start=(context.stage_entered_at),
        window_end=(observed_at),
    )

    coverage_blockers = crm_coverage.blockers + openlines_coverage.blockers + call_coverage.blockers

    if coverage_blockers:
        evaluation = _blocked(
            deal_id=deal_id,
            stage_id=(context.stage_id),
            reasons=(coverage_blockers),
            evidence=(context.stage_evidence,),
            details={
                "crm_complete": crm_coverage.complete,
                "openlines_complete": openlines_coverage.complete,
                "call_complete": call_coverage.complete,
            },
        )

        result = _empty_result(
            evaluation=evaluation,
            stage_id=(context.stage_id),
            stage_entry_source=(entry_source),
            blockers=(coverage_blockers),
            crm_coverage=(crm_coverage),
            openlines_coverage=(openlines_coverage),
            call_coverage=(call_coverage),
        )

        return result

    (
        messages,
        message_blockers,
    ) = _message_candidates(
        database_path,
        deal_id=deal_id,
        start=(context.stage_entered_at),
        end=observed_at,
    )

    emails = _email_candidates(
        database_path,
        deal_id=deal_id,
        start=(context.stage_entered_at),
        end=observed_at,
    )

    (
        calls,
        call_blockers,
        relevant_successful_calls,
    ) = _call_candidates(
        database_path,
        deal_id=deal_id,
        start=(context.stage_entered_at),
        end=observed_at,
    )

    blockers = message_blockers + call_blockers

    if blockers:
        evaluation = _blocked(
            deal_id=deal_id,
            stage_id=(context.stage_id),
            reasons=blockers,
            evidence=(context.stage_evidence,),
        )

        return RealtimeDealStageEvaluation(
            evaluation=evaluation,
            stage_id=(context.stage_id),
            stage_entry_source=(entry_source),
            last_qualifying_activity_kind="",
            last_qualifying_activity_source="",
            relevant_successful_call_ids=(relevant_successful_calls),
            build_blockers=blockers,
            crm_coverage=(crm_coverage),
            openlines_coverage=(openlines_coverage),
            call_coverage=(call_coverage),
        )

    candidates = messages + emails + calls

    last_activity = (
        max(
            candidates,
            key=lambda item: (
                item.occurred_at,
                item.evidence.source_id,
            ),
        )
        if candidates
        else None
    )

    case = StageTimerCase(
        deal_id=deal_id,
        stage_id=(context.stage_id),
        stage_entered_at=(context.stage_entered_at),
        as_of=observed_at,
        stage_entry_evidence=(context.stage_evidence),
        last_qualifying_activity_at=(
            last_activity.occurred_at if last_activity is not None else None
        ),
        last_activity_evidence=(last_activity.evidence if last_activity is not None else None),
        last_activity_kind=(last_activity.kind if last_activity is not None else ""),
    )

    evaluation = evaluate_stage_timer_case(case)

    return RealtimeDealStageEvaluation(
        evaluation=evaluation,
        stage_id=(context.stage_id),
        stage_entry_source=(entry_source),
        last_qualifying_activity_kind=(last_activity.kind if last_activity is not None else ""),
        last_qualifying_activity_source=(
            last_activity.source_name if last_activity is not None else ""
        ),
        relevant_successful_call_ids=(relevant_successful_calls),
        build_blockers=(),
        crm_coverage=(crm_coverage),
        openlines_coverage=(openlines_coverage),
        call_coverage=(call_coverage),
    )
