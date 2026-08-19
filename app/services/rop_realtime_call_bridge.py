from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.rop_policy_evaluation import (
    EvidenceRef,
)
from app.services.rop_response_evidence_adapter import (
    ExactCallResponse,
)

VALID_CALL_TYPES = frozenset(
    {
        "1",
        "2",
    }
)


@dataclass(frozen=True, slots=True)
class RealtimeExactCallBuildResult:
    exact_calls: tuple[ExactCallResponse, ...]
    blockers: tuple[str, ...]
    successful_call_ids: tuple[str, ...]
    failed_call_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CallEvidenceRow:
    call_id: str
    event_name: str
    event_ts: int
    actor_user_id: str
    call_failed_code: str
    call_duration_seconds: int | None
    crm_activity_id: str
    crm_entity_type: str
    crm_entity_id: str
    data: dict[str, Any]


def _connect_read_only(
    database_path: str,
) -> sqlite3.Connection:
    path = Path(database_path).resolve()

    if not path.exists():
        raise FileNotFoundError("realtime_call_database_missing")

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


def _text(
    value: Any,
) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _positive_int(
    value: Any,
) -> int | None:
    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ):
        return None

    if result <= 0:
        return None

    return result


def _payload(
    value: Any,
) -> dict[str, Any]:
    if not isinstance(
        value,
        str,
    ):
        return {}

    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return {}

    if not isinstance(
        result,
        dict,
    ):
        return {}

    return result


def _load_rows(
    connection: sqlite3.Connection,
) -> list[_CallEvidenceRow]:
    rows = connection.execute(
        """
        SELECT
            evidence.call_id,
            evidence.event_name,
            evidence.event_ts,
            evidence.actor_user_id,
            evidence.call_failed_code,
            evidence.call_duration_seconds,
            evidence.crm_activity_id,
            evidence.crm_entity_type,
            evidence.crm_entity_id,
            inbox.data_json
        FROM bitrix_call_evidence
            AS evidence
        LEFT JOIN bitrix_event_inbox
            AS inbox
          ON inbox.id = evidence.inbox_id
        WHERE evidence.call_id IS NOT NULL
          AND evidence.call_id <> ''
        ORDER BY
            evidence.call_id,
            evidence.event_ts,
            evidence.inbox_id
        """
    ).fetchall()

    result: list[_CallEvidenceRow] = []

    for row in rows:
        duration_raw = row["call_duration_seconds"]

        try:
            duration = int(duration_raw) if duration_raw is not None else None
        except (
            TypeError,
            ValueError,
        ):
            duration = None

        result.append(
            _CallEvidenceRow(
                call_id=_text(row["call_id"]),
                event_name=_text(row["event_name"]),
                event_ts=int(row["event_ts"]),
                actor_user_id=_text(row["actor_user_id"]),
                call_failed_code=_text(row["call_failed_code"]),
                call_duration_seconds=duration,
                crm_activity_id=_text(row["crm_activity_id"]),
                crm_entity_type=_text(row["crm_entity_type"]).upper(),
                crm_entity_id=_text(row["crm_entity_id"]),
                data=_payload(row["data_json"]),
            )
        )

    return result


def _event_user_id(
    row: _CallEvidenceRow,
) -> int | None:
    direct = _positive_int(row.actor_user_id)

    if direct is not None:
        return direct

    return _positive_int(row.data.get("USER_ID"))


def _event_call_type(
    row: _CallEvidenceRow,
) -> str:
    value = _text(row.data.get("CALL_TYPE"))

    if value in VALID_CALL_TYPES:
        return value

    return ""


def _first_nonempty(
    *values: str,
) -> str:
    for value in values:
        if value:
            return value

    return ""


def _build_one(
    call_id: str,
    rows: list[_CallEvidenceRow],
) -> tuple[
    ExactCallResponse | None,
    tuple[str, ...],
    bool,
]:
    ends = [row for row in rows if (row.event_name == "ONVOXIMPLANTCALLEND")]

    successful_ends = [row for row in ends if (row.call_failed_code == "200")]

    if not successful_ends:
        return (
            None,
            (),
            bool(ends),
        )

    successful_end = min(
        successful_ends,
        key=lambda row: (
            row.event_ts,
            row.call_id,
        ),
    )

    starts = [
        row
        for row in rows
        if (row.event_name == "ONVOXIMPLANTCALLSTART" and row.event_ts <= successful_end.event_ts)
    ]

    if not starts:
        return (
            None,
            ("successful_call_missing_exact_start:" + call_id,),
            False,
        )

    start = min(
        starts,
        key=lambda row: (
            row.event_ts,
            row.call_id,
        ),
    )

    start_user_id = _event_user_id(start)

    end_user_id = _event_user_id(successful_end)

    if start_user_id is None:
        return (
            None,
            ("successful_call_start_user_missing:" + call_id,),
            False,
        )

    if end_user_id is not None and end_user_id != start_user_id:
        return (
            None,
            ("successful_call_user_mismatch:" + call_id,),
            False,
        )

    observed_types = {
        value
        for value in (
            _event_call_type(start),
            _event_call_type(successful_end),
        )
        if value
    }

    if not observed_types:
        return (
            None,
            ("successful_call_type_missing_or_invalid:" + call_id,),
            False,
        )

    if len(observed_types) != 1:
        return (
            None,
            ("successful_call_type_mismatch:" + call_id,),
            False,
        )

    crm_activity_id = _first_nonempty(
        successful_end.crm_activity_id,
        start.crm_activity_id,
    )

    crm_entity_type = _first_nonempty(
        successful_end.crm_entity_type,
        start.crm_entity_type,
    ).upper()

    crm_entity_id = _first_nonempty(
        successful_end.crm_entity_id,
        start.crm_entity_id,
    )

    if not crm_activity_id and not (crm_entity_type and crm_entity_id):
        return (
            None,
            ("successful_call_crm_link_missing:" + call_id,),
            False,
        )

    occurred_at = datetime.fromtimestamp(
        start.event_ts,
        tz=UTC,
    )

    evidence = EvidenceRef(
        source_type=("voximplant_call_start_event"),
        source_id=call_id,
        occurred_at=occurred_at,
        event_kind=("successful_phone_conversation_start"),
        actor_kind=("directory_user"),
        actor_id=start_user_id,
    )

    return (
        ExactCallResponse(
            call_id=call_id,
            crm_activity_id=crm_activity_id,
            crm_entity_type=crm_entity_type,
            crm_entity_id=crm_entity_id,
            response_at=occurred_at,
            manager_user_id=(start_user_id),
            evidence=evidence,
        ),
        (),
        False,
    )


def build_exact_realtime_call_responses(
    database_path: str,
) -> RealtimeExactCallBuildResult:
    try:
        connection = _connect_read_only(database_path)
    except FileNotFoundError:
        return RealtimeExactCallBuildResult(
            exact_calls=(),
            blockers=("realtime_call_database_missing",),
            successful_call_ids=(),
            failed_call_ids=(),
        )

    try:
        objects = _objects(connection)

        required = {
            "bitrix_call_evidence",
            "bitrix_event_inbox",
        }

        if not required.issubset(objects):
            return RealtimeExactCallBuildResult(
                exact_calls=(),
                blockers=("realtime_call_evidence_tables_missing",),
                successful_call_ids=(),
                failed_call_ids=(),
            )

        rows = _load_rows(connection)

    finally:
        connection.close()

    grouped: dict[
        str,
        list[_CallEvidenceRow],
    ] = defaultdict(list)

    for row in rows:
        grouped[row.call_id].append(row)

    exact_calls: list[ExactCallResponse] = []

    blockers: list[str] = []

    successful_ids: list[str] = []

    failed_ids: list[str] = []

    for call_id in sorted(grouped):
        rows_for_call = grouped[call_id]

        has_success = any(
            (row.event_name == "ONVOXIMPLANTCALLEND" and row.call_failed_code == "200")
            for row in rows_for_call
        )

        exact, call_blockers, failed = _build_one(
            call_id,
            rows_for_call,
        )

        if has_success:
            successful_ids.append(call_id)

        if failed:
            failed_ids.append(call_id)

        blockers.extend(call_blockers)

        if exact is not None:
            exact_calls.append(exact)

    exact_calls.sort(
        key=lambda item: (
            item.response_at,
            item.call_id,
        )
    )

    return RealtimeExactCallBuildResult(
        exact_calls=tuple(exact_calls),
        blockers=tuple(sorted(blockers)),
        successful_call_ids=tuple(sorted(successful_ids)),
        failed_call_ids=tuple(sorted(failed_ids)),
    )
