from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.services.rop_policy_evaluation import (
    EvidenceRef,
    FirstResponseCase,
)


class TimingQuality(StrEnum):
    EXACT = "exact"
    CALL_START_ONLY = "call_start_only"


@dataclass(frozen=True, slots=True)
class SuccessfulCallFact:
    call_id: str
    crm_activity_id: str
    crm_entity_type: str
    crm_entity_id: str
    portal_user_id: int | None
    call_type: str
    call_start_at: datetime
    call_duration_seconds: int
    timing_quality: TimingQuality


@dataclass(frozen=True, slots=True)
class ExactCallResponse:
    call_id: str
    crm_activity_id: str
    crm_entity_type: str
    crm_entity_id: str
    response_at: datetime
    manager_user_id: int
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True)
class FirstResponseBuildResult:
    case: FirstResponseCase | None
    blockers: tuple[str, ...]
    exact_response_source: str
    supporting_successful_call_ids: tuple[str, ...]


def _datetime(
    value: object,
) -> datetime | None:
    if value in (None, ""):
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

    return parsed.astimezone(UTC)


def normalize_successful_call_statistic(
    record: dict[str, Any],
) -> SuccessfulCallFact | None:
    if str(record.get("CALL_FAILED_CODE") or "") != "200":
        return None

    call_type = str(record.get("CALL_TYPE") or "")

    if call_type not in {
        "1",
        "2",
    }:
        return None

    call_id = str(record.get("CALL_ID") or "").strip()

    started = _datetime(record.get("CALL_START_DATE"))

    if not call_id or started is None:
        return None

    user_raw = record.get("PORTAL_USER_ID")

    try:
        user_id = (
            int(user_raw)
            if user_raw
            not in (
                None,
                "",
            )
            else None
        )
    except (
        TypeError,
        ValueError,
    ):
        user_id = None

    try:
        duration = int(record.get("CALL_DURATION") or 0)
    except (
        TypeError,
        ValueError,
    ):
        duration = 0

    return SuccessfulCallFact(
        call_id=call_id,
        crm_activity_id=str(record.get("CRM_ACTIVITY_ID") or ""),
        crm_entity_type=str(record.get("CRM_ENTITY_TYPE") or "").strip().upper(),
        crm_entity_id=str(record.get("CRM_ENTITY_ID") or "").strip(),
        portal_user_id=user_id,
        call_type=call_type,
        call_start_at=started,
        call_duration_seconds=max(
            0,
            duration,
        ),
        timing_quality=(TimingQuality.CALL_START_ONLY),
    )


def merge_call_start_event(
    event: dict[str, Any],
    call: SuccessfulCallFact,
) -> ExactCallResponse | None:
    data = event.get("data")

    if not isinstance(
        data,
        dict,
    ):
        return None

    call_id = str(data.get("CALL_ID") or "").strip()

    if call_id != call.call_id:
        return None

    user_raw = data.get("USER_ID")

    try:
        user_id = int(user_raw)
    except (
        TypeError,
        ValueError,
    ):
        return None

    if call.portal_user_id is not None and user_id != call.portal_user_id:
        return None

    try:
        event_ts = int(event.get("ts"))
    except (
        TypeError,
        ValueError,
    ):
        return None

    occurred_at = datetime.fromtimestamp(
        event_ts,
        tz=UTC,
    )

    evidence = EvidenceRef(
        source_type=("voximplant_call_start_event"),
        source_id=call.call_id,
        occurred_at=occurred_at,
        event_kind=("successful_phone_conversation_start"),
        actor_kind="directory_user",
        actor_id=user_id,
    )

    return ExactCallResponse(
        call_id=call.call_id,
        crm_activity_id=(call.crm_activity_id),
        crm_entity_type=(call.crm_entity_type),
        crm_entity_id=(call.crm_entity_id),
        response_at=occurred_at,
        manager_user_id=user_id,
        evidence=evidence,
    )


def _connect_read_only(
    database_path: str,
) -> sqlite3.Connection:
    path = Path(database_path).resolve()

    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
    )

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
) -> str:
    if "crm_active_entities" in objects:
        return "crm_active_entities"

    if "crm_raw_entities" in objects:
        return "crm_raw_entities"

    raise ValueError("crm_source_missing")


def _load_lead_created(
    connection: sqlite3.Connection,
    *,
    source: str,
    lead_id: int,
) -> (
    tuple[
        datetime,
        EvidenceRef,
    ]
    | None
):
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
        payload = json.loads(row[0])
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
            source_type="crm_lead",
            source_id=str(lead_id),
            occurred_at=created,
            event_kind="lead_created",
        ),
    )


def _manager_message_candidates(
    connection: sqlite3.Connection,
    *,
    lead_id: int,
    lead_created_at: datetime,
) -> list[
    tuple[
        datetime,
        EvidenceRef,
    ]
]:
    rows = connection.execute(
        """
        SELECT
            msg.message_id,
            msg.sent_at,
            msg.sender_directory_user_id
        FROM openlines_crm_links AS link
        JOIN openlines_messages AS msg
          ON msg.chat_id = link.chat_id
        WHERE link.entity_type = 'lead'
          AND link.entity_id = ?
          AND msg.sender_role = 'manager'
          AND msg.sender_directory_user_id
              IS NOT NULL
          AND msg.sent_at IS NOT NULL
        """,
        (str(lead_id),),
    ).fetchall()

    result = []

    for (
        message_id,
        sent_at,
        user_id_raw,
    ) in rows:
        occurred_at = _datetime(sent_at)

        if occurred_at is None or occurred_at < lead_created_at:
            continue

        try:
            user_id = int(user_id_raw)
        except (
            TypeError,
            ValueError,
        ):
            continue

        result.append(
            (
                occurred_at,
                EvidenceRef(
                    source_type=("openlines_message"),
                    source_id=str(message_id),
                    occurred_at=occurred_at,
                    event_kind=("manager_response"),
                    actor_kind=("directory_user"),
                    actor_id=user_id,
                ),
            )
        )

    return result


def _activity_links_to_lead(
    connection: sqlite3.Connection,
    *,
    source: str,
    activity_id: str,
    lead_id: int,
) -> bool:
    if not activity_id:
        return False

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
        return False

    try:
        payload = json.loads(row[0])
    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return False

    if not isinstance(
        payload,
        dict,
    ):
        return False

    owner_type = str(payload.get("OWNER_TYPE_ID") or "")

    owner_id = str(payload.get("OWNER_ID") or "")

    return owner_type == "1" and owner_id == str(lead_id)


def _call_applies_to_lead(
    connection: sqlite3.Connection,
    *,
    source: str,
    lead_id: int,
    crm_entity_type: str,
    crm_entity_id: str,
    crm_activity_id: str,
) -> bool:
    if crm_entity_type == "LEAD" and crm_entity_id == str(lead_id):
        return True

    return _activity_links_to_lead(
        connection,
        source=source,
        activity_id=crm_activity_id,
        lead_id=lead_id,
    )


def build_first_response_case_from_sources(
    database_path: str,
    *,
    lead_id: int,
    exact_calls: tuple[ExactCallResponse, ...] = (),
    successful_call_facts: tuple[SuccessfulCallFact, ...] = (),
    openlines_source_complete: bool,
    call_source_complete: bool,
    as_of: datetime,
) -> FirstResponseBuildResult:
    connection = _connect_read_only(database_path)

    try:
        objects = _objects(connection)

        source = _crm_source(objects)

        lead = _load_lead_created(
            connection,
            source=source,
            lead_id=lead_id,
        )

        if lead is None:
            return FirstResponseBuildResult(
                case=None,
                blockers=("lead_creation_evidence_missing",),
                exact_response_source="",
                supporting_successful_call_ids=(),
            )

        (
            lead_created_at,
            lead_evidence,
        ) = lead

        blockers = []

        required_openlines = {
            "openlines_crm_links",
            "openlines_messages",
        }

        if not required_openlines.issubset(objects):
            blockers.append("openlines_tables_missing")

        if not openlines_source_complete:
            blockers.append("openlines_source_not_complete")

        if not call_source_complete:
            blockers.append("call_source_not_complete")

        if blockers:
            return FirstResponseBuildResult(
                case=None,
                blockers=tuple(blockers),
                exact_response_source="",
                supporting_successful_call_ids=(),
            )

        messages = _manager_message_candidates(
            connection,
            lead_id=lead_id,
            lead_created_at=(lead_created_at),
        )

        exact_candidates = [
            (
                occurred_at,
                evidence,
                "openlines_message",
            )
            for (
                occurred_at,
                evidence,
            ) in messages
        ]

        for call in exact_calls:
            if call.response_at < lead_created_at:
                continue

            if not _call_applies_to_lead(
                connection,
                source=source,
                lead_id=lead_id,
                crm_entity_type=(call.crm_entity_type),
                crm_entity_id=(call.crm_entity_id),
                crm_activity_id=(call.crm_activity_id),
            ):
                continue

            exact_candidates.append(
                (
                    call.response_at,
                    call.evidence,
                    ("voximplant_call_start_event"),
                )
            )

        supporting = []

        for call in successful_call_facts:
            if call.call_start_at < lead_created_at:
                continue

            if _call_applies_to_lead(
                connection,
                source=source,
                lead_id=lead_id,
                crm_entity_type=(call.crm_entity_type),
                crm_entity_id=(call.crm_entity_id),
                crm_activity_id=(call.crm_activity_id),
            ):
                supporting.append(call.call_id)

        exact_call_ids = {call.call_id for call in exact_calls}

        unresolved_supporting = [call_id for call_id in supporting if call_id not in exact_call_ids]

        if exact_candidates:
            exact_candidates.sort(
                key=lambda item: (
                    item[0],
                    item[1].source_id,
                )
            )

            (
                response_at,
                response_evidence,
                source_name,
            ) = exact_candidates[0]

            return FirstResponseBuildResult(
                case=FirstResponseCase(
                    lead_id=lead_id,
                    lead_created_at=(lead_created_at),
                    lead_created_evidence=(lead_evidence),
                    manager_response_at=(response_at),
                    manager_response_evidence=(response_evidence),
                ),
                blockers=(),
                exact_response_source=(source_name),
                supporting_successful_call_ids=tuple(sorted(unresolved_supporting)),
            )

        if unresolved_supporting:
            return FirstResponseBuildResult(
                case=None,
                blockers=("successful_call_present_but_exact_answer_time_missing",),
                exact_response_source="",
                supporting_successful_call_ids=tuple(sorted(unresolved_supporting)),
            )

        return FirstResponseBuildResult(
            case=FirstResponseCase(
                lead_id=lead_id,
                lead_created_at=(lead_created_at),
                lead_created_evidence=(lead_evidence),
                as_of=as_of,
            ),
            blockers=(),
            exact_response_source=("no_response_observed"),
            supporting_successful_call_ids=(),
        )

    finally:
        connection.close()
