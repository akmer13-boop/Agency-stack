from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.rop_business_time import (
    TimerStatus,
    evaluate_stage_timer,
)
from app.services.rop_policy_scope import resolve_policy_scope


TRACKED_STAGE_IDS = (
    "C7:NEW",
    "C7:PREPARATION",
    "C7:PREPAYMENT_INVOICE",
    "C7:UC_IAVLST",
    "C7:EXECUTING",
    "C7:FINAL_INVOICE",
)

STAGE_LABELS = {
    "C7:NEW": "Новая",
    "C7:PREPARATION": "Выявление потребностей",
    "C7:PREPAYMENT_INVOICE": "Подбор пакетного тура",
    "C7:UC_IAVLST": "Запрос отправлен партнеру",
    "C7:EXECUTING": "КП отправлено",
    "C7:FINAL_INVOICE": "Потенциальный клиент",
}

_OWNER_TYPE_TO_ENTITY = {
    "1": "lead",
    "2": "deal",
    "3": "contact",
    "4": "company",
}


@dataclass(frozen=True, slots=True)
class StageSlaDealTruth:
    deal_id: int
    stage_id: str
    stage_label: str
    status: str
    manager_id: str
    manager_name: str
    stage_entered_at: datetime | None
    anchor_at: datetime | None
    deadline_at: datetime | None
    last_qualifying_activity_at: datetime | None
    last_qualifying_activity_kind: str
    blocker_reason: str

    @property
    def requires_attention(self) -> bool:
        return self.status == "ATTENTION"


@dataclass(frozen=True, slots=True)
class B2CStageSlaTruth:
    cutoff_at: datetime
    crm_run_id: int | None
    openlines_last_at: datetime | None
    vox_run_id: int | None
    vox_window_start: datetime | None
    vox_window_end: datetime | None
    tracked_deals: int
    open: int
    attention: int
    blocked: int
    by_stage: tuple[tuple[str, int, int, int, int], ...]
    blocked_reasons: tuple[tuple[str, int], ...]
    attention_by_manager: tuple[tuple[str, str, int], ...]
    deals: tuple[StageSlaDealTruth, ...]


def _connect(database_path: str) -> sqlite3.Connection:
    path = Path(database_path).resolve()
    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _dt(value: Any, *, naive_utc: bool = False) -> datetime | None:
    if value in (None, ""):
        return None

    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        if not naive_utc:
            parsed = parsed.replace(tzinfo=UTC)
        else:
            parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def _entity_ids(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(
            sorted(
                {
                    str(item).strip()
                    for item in value
                    if item not in (None, "")
                    and str(item).strip()
                }
            )
        )
    text = str(value).strip()
    return (text,) if text else ()


def _completed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {
        "Y",
        "YES",
        "TRUE",
        "1",
    }


def _activity_time(payload: dict[str, Any]) -> datetime | None:
    for key in (
        "END_TIME",
        "START_TIME",
        "LAST_UPDATED",
        "CREATED",
        "DEADLINE",
    ):
        result = _dt(payload.get(key))
        if result is not None:
            return result
    return None


def _manager_name(payload: dict[str, Any] | None, user_id: str) -> str:
    if payload is None:
        return f"Менеджер #{user_id}" if user_id else "Не назначен"

    parts = [
        str(payload.get("NAME") or "").strip(),
        str(payload.get("LAST_NAME") or "").strip(),
    ]
    name = " ".join(part for part in parts if part).strip()
    if name:
        return name

    login = str(payload.get("LOGIN") or "").strip()
    if login:
        return login

    return f"Менеджер #{user_id}" if user_id else "Не назначен"


def _current_cutoff(
    connection: sqlite3.Connection,
) -> tuple[
    datetime,
    int | None,
    datetime | None,
    int | None,
    datetime | None,
    datetime | None,
]:
    crm_row = connection.execute(
        """
        SELECT id, finished_at
        FROM crm_sync_runs
        WHERE status = 'completed'
          AND finished_at IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    if crm_row is None:
        raise ValueError("crm_complete_sync_missing")

    crm_finished = _dt(
        crm_row["finished_at"],
        naive_utc=True,
    )
    if crm_finished is None:
        raise ValueError("crm_complete_sync_timestamp_invalid")

    openlines_row = connection.execute(
        """
        SELECT MAX(sent_at) AS last_at
        FROM openlines_messages
        WHERE sent_at IS NOT NULL
        """
    ).fetchone()
    openlines_last = (
        _dt(openlines_row["last_at"])
        if openlines_row is not None
        else None
    )

    vox_row = connection.execute(
        """
        SELECT id, window_start, window_end
        FROM rop_voximplant_reconciliation_runs
        WHERE pagination_complete = 1
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    vox_run_id: int | None = None
    vox_start: datetime | None = None
    vox_end: datetime | None = None

    if vox_row is not None:
        vox_run_id = int(vox_row["id"])
        vox_start = _dt(vox_row["window_start"])
        vox_end = _dt(vox_row["window_end"])

    candidates = [crm_finished]
    if openlines_last is not None:
        candidates.append(openlines_last)
    if vox_end is not None:
        candidates.append(vox_end)

    return (
        min(candidates),
        int(crm_row["id"]),
        openlines_last,
        vox_run_id,
        vox_start,
        vox_end,
    )


def _load_entities(
    connection: sqlite3.Connection,
    entity_type: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    rows = connection.execute(
        """
        SELECT entity_id, payload_json
        FROM crm_active_entities
        WHERE entity_type = ?
        """,
        (entity_type,),
    ).fetchall()

    for row in rows:
        item = _payload(row["payload_json"])
        if item is not None:
            result[str(row["entity_id"])] = item

    return result


def _candidate_map(
    deals: dict[int, dict[str, Any]],
) -> dict[tuple[str, str], frozenset[int]]:
    mutable: dict[tuple[str, str], set[int]] = {}

    for deal_id, item in deals.items():
        keys = [("deal", str(deal_id))]

        for lead_id in _entity_ids(item.get("LEAD_ID")):
            keys.append(("lead", lead_id))

        for raw in (
            item.get("CONTACT_ID"),
            item.get("CONTACT_IDS"),
        ):
            for contact_id in _entity_ids(raw):
                keys.append(("contact", contact_id))

        for company_id in _entity_ids(item.get("COMPANY_ID")):
            keys.append(("company", company_id))

        for key in keys:
            mutable.setdefault(key, set()).add(deal_id)

    return {
        key: frozenset(values)
        for key, values in mutable.items()
    }


def _resolved_chat_map(
    connection: sqlite3.Connection,
    candidates: dict[tuple[str, str], frozenset[int]],
) -> dict[str, int]:
    chat_candidates: dict[str, set[int]] = {}

    rows = connection.execute(
        """
        SELECT chat_id, entity_type, entity_id
        FROM openlines_crm_links
        """
    ).fetchall()

    for row in rows:
        entity_type = str(row["entity_type"] or "").strip().lower()
        entity_id = str(row["entity_id"] or "").strip()
        values = candidates.get(
            (entity_type, entity_id),
            frozenset(),
        )
        if values:
            chat_candidates.setdefault(
                str(row["chat_id"]),
                set(),
            ).update(values)

    return {
        chat_id: next(iter(values))
        for chat_id, values in chat_candidates.items()
        if len(values) == 1
    }


def _stage_entry(
    connection: sqlite3.Connection,
    *,
    deal_id: int,
    deal: dict[str, Any],
    stage_id: str,
    cutoff: datetime,
) -> tuple[datetime | None, str]:
    rows = connection.execute(
        """
        SELECT entity_id, payload_json
        FROM crm_active_entities
        WHERE entity_type = 'deal_stage_history'
        """
    ).fetchall()

    observed: list[tuple[datetime, str]] = []

    for row in rows:
        item = _payload(row["payload_json"])
        if item is None:
            continue
        if str(item.get("OWNER_ID") or "") != str(deal_id):
            continue
        if str(item.get("STAGE_ID") or "") != stage_id:
            continue

        occurred = _dt(item.get("CREATED_TIME"))
        if occurred is None or occurred > cutoff:
            continue

        observed.append(
            (occurred, str(row["entity_id"]))
        )

    if observed:
        occurred, _ = max(
            observed,
            key=lambda item: (item[0], item[1]),
        )
        return occurred, ""

    moved_at = _dt(deal.get("MOVED_TIME"))
    if moved_at is not None:
        if moved_at <= cutoff:
            return moved_at, ""
        return None, "current_stage_not_valid_at_cutoff"

    return None, "stage_entry_evidence_missing_at_cutoff"


def _qualifying_activities(
    connection: sqlite3.Connection,
    *,
    candidates: dict[tuple[str, str], frozenset[int]],
    chat_map: dict[str, int],
    start: datetime,
    end: datetime,
) -> dict[int, list[tuple[datetime, str]]]:
    result: dict[int, list[tuple[datetime, str]]] = {}

    message_rows = connection.execute(
        """
        SELECT
            chat_id,
            sent_at
        FROM openlines_messages
        WHERE sender_role = 'manager'
          AND sender_directory_user_id IS NOT NULL
          AND sent_at IS NOT NULL
        """
    ).fetchall()

    for row in message_rows:
        deal_id = chat_map.get(str(row["chat_id"]))
        if deal_id is None:
            continue

        occurred = _dt(row["sent_at"])
        if occurred is None or occurred < start or occurred > end:
            continue

        result.setdefault(deal_id, []).append(
            (occurred, "message")
        )

    activity_rows = connection.execute(
        """
        SELECT entity_id, payload_json
        FROM crm_active_entities
        WHERE entity_type = 'activity'
        """
    ).fetchall()

    for row in activity_rows:
        item = _payload(row["payload_json"])
        if item is None:
            continue

        if str(item.get("TYPE_ID") or "") != "4":
            continue
        if str(item.get("DIRECTION") or "") != "2":
            continue
        if not _completed(item.get("COMPLETED")):
            continue

        owner_type = _OWNER_TYPE_TO_ENTITY.get(
            str(item.get("OWNER_TYPE_ID") or "").strip()
        )
        owner_id = str(item.get("OWNER_ID") or "").strip()

        if not owner_type or not owner_id:
            continue

        deal_ids = candidates.get(
            (owner_type, owner_id),
            frozenset(),
        )
        if len(deal_ids) != 1:
            continue

        occurred = _activity_time(item)
        if occurred is None or occurred < start or occurred > end:
            continue

        deal_id = next(iter(deal_ids))
        result.setdefault(deal_id, []).append(
            (occurred, "email")
        )

    return result


def _successful_calls(
    connection: sqlite3.Connection,
    *,
    run_id: int | None,
    candidates: dict[tuple[str, str], frozenset[int]],
) -> dict[int, list[datetime]]:
    if run_id is None:
        return {}

    activity_owner: dict[str, tuple[str, str]] = {}

    for row in connection.execute(
        """
        SELECT entity_id, payload_json
        FROM crm_active_entities
        WHERE entity_type = 'activity'
        """
    ):
        item = _payload(row["payload_json"])
        if item is None:
            continue

        owner_type = _OWNER_TYPE_TO_ENTITY.get(
            str(item.get("OWNER_TYPE_ID") or "").strip()
        )
        owner_id = str(item.get("OWNER_ID") or "").strip()

        if owner_type and owner_id:
            activity_owner[str(row["entity_id"])] = (
                owner_type,
                owner_id,
            )

    result: dict[int, list[datetime]] = {}

    rows = connection.execute(
        """
        SELECT
            call_start_at,
            crm_activity_id,
            crm_entity_type,
            crm_entity_id
        FROM rop_voximplant_statistic_facts
        WHERE last_seen_run_id = ?
          AND call_failed_code = '200'
        """,
        (run_id,),
    ).fetchall()

    for row in rows:
        direct_type = str(
            row["crm_entity_type"] or ""
        ).strip().lower()
        direct_id = str(
            row["crm_entity_id"] or ""
        ).strip()

        combined: set[int] = set()

        if direct_type and direct_id:
            combined.update(
                candidates.get(
                    (direct_type, direct_id),
                    frozenset(),
                )
            )

        activity_key = activity_owner.get(
            str(row["crm_activity_id"] or "")
        )
        if activity_key is not None:
            combined.update(
                candidates.get(
                    activity_key,
                    frozenset(),
                )
            )

        if len(combined) != 1:
            continue

        occurred = _dt(row["call_start_at"])
        if occurred is None:
            continue

        deal_id = next(iter(combined))
        result.setdefault(deal_id, []).append(
            occurred
        )

    return result


def build_b2c_stage_sla_truth(
    database_path: str,
) -> B2CStageSlaTruth:
    connection = _connect(database_path)

    try:
        (
            cutoff,
            crm_run_id,
            openlines_last,
            vox_run_id,
            vox_start,
            vox_end,
        ) = _current_cutoff(connection)

        raw_deals = _load_entities(
            connection,
            "deal",
        )
        users = _load_entities(
            connection,
            "user",
        )

        tracked_deals: dict[int, dict[str, Any]] = {}

        for raw_id, deal in raw_deals.items():
            try:
                deal_id = int(raw_id)
            except (TypeError, ValueError):
                continue

            stage_id = str(
                deal.get("STAGE_ID") or ""
            ).strip()

            if stage_id not in TRACKED_STAGE_IDS:
                continue

            decision = resolve_policy_scope(
                database_path,
                entity_type="deal",
                entity_id=deal_id,
            )

            if not decision.eligible:
                continue

            tracked_deals[deal_id] = deal

        candidates = _candidate_map(tracked_deals)
        chat_map = _resolved_chat_map(
            connection,
            candidates,
        )

        stage_entries: dict[int, tuple[datetime | None, str]] = {}
        earliest_entry: datetime | None = None

        for deal_id, deal in tracked_deals.items():
            stage_id = str(
                deal.get("STAGE_ID") or ""
            ).strip()

            entry, blocker = _stage_entry(
                connection,
                deal_id=deal_id,
                deal=deal,
                stage_id=stage_id,
                cutoff=cutoff,
            )

            stage_entries[deal_id] = (
                entry,
                blocker,
            )

            if entry is not None:
                earliest_entry = (
                    entry
                    if earliest_entry is None
                    else min(earliest_entry, entry)
                )

        activity_start = (
            earliest_entry
            if earliest_entry is not None
            else cutoff
        )

        activities = _qualifying_activities(
            connection,
            candidates=candidates,
            chat_map=chat_map,
            start=activity_start,
            end=cutoff,
        )

        calls = _successful_calls(
            connection,
            run_id=vox_run_id,
            candidates=candidates,
        )

        rows: list[StageSlaDealTruth] = []
        blocked_reasons: Counter[str] = Counter()
        manager_attention: Counter[
            tuple[str, str]
        ] = Counter()

        for deal_id, deal in tracked_deals.items():
            stage_id = str(
                deal.get("STAGE_ID") or ""
            ).strip()
            label = STAGE_LABELS.get(
                stage_id,
                stage_id,
            )

            manager_id = str(
                deal.get("ASSIGNED_BY_ID")
                or deal.get("RESPONSIBLE_ID")
                or ""
            ).strip()
            manager_name = _manager_name(
                users.get(manager_id),
                manager_id,
            )

            entry, entry_blocker = stage_entries[
                deal_id
            ]

            if stage_id == "C7:FINAL_INVOICE":
                reason = (
                    "return_to_client_date_not_configured"
                )
                blocked_reasons[reason] += 1
                rows.append(
                    StageSlaDealTruth(
                        deal_id=deal_id,
                        stage_id=stage_id,
                        stage_label=label,
                        status="BLOCKED",
                        manager_id=manager_id,
                        manager_name=manager_name,
                        stage_entered_at=entry,
                        anchor_at=None,
                        deadline_at=None,
                        last_qualifying_activity_at=None,
                        last_qualifying_activity_kind="",
                        blocker_reason=reason,
                    )
                )
                continue

            if entry is None:
                reason = (
                    entry_blocker
                    or "stage_entry_evidence_missing_at_cutoff"
                )
                blocked_reasons[reason] += 1
                rows.append(
                    StageSlaDealTruth(
                        deal_id=deal_id,
                        stage_id=stage_id,
                        stage_label=label,
                        status="BLOCKED",
                        manager_id=manager_id,
                        manager_name=manager_name,
                        stage_entered_at=None,
                        anchor_at=None,
                        deadline_at=None,
                        last_qualifying_activity_at=None,
                        last_qualifying_activity_kind="",
                        blocker_reason=reason,
                    )
                )
                continue

            relevant = [
                item
                for item in activities.get(
                    deal_id,
                    [],
                )
                if entry <= item[0] <= cutoff
            ]

            last_activity_at: datetime | None = None
            last_activity_kind = ""

            if relevant:
                (
                    last_activity_at,
                    last_activity_kind,
                ) = max(
                    relevant,
                    key=lambda item: item[0],
                )

            evaluation = evaluate_stage_timer(
                stage_id=stage_id,
                stage_entered_at=entry,
                as_of=cutoff,
                last_qualifying_activity_at=(
                    last_activity_at
                ),
            )

            if evaluation.status is TimerStatus.OPEN:
                rows.append(
                    StageSlaDealTruth(
                        deal_id=deal_id,
                        stage_id=stage_id,
                        stage_label=label,
                        status="OPEN",
                        manager_id=manager_id,
                        manager_name=manager_name,
                        stage_entered_at=entry,
                        anchor_at=evaluation.anchor_at,
                        deadline_at=evaluation.deadline_at,
                        last_qualifying_activity_at=(
                            last_activity_at
                        ),
                        last_qualifying_activity_kind=(
                            last_activity_kind
                        ),
                        blocker_reason="",
                    )
                )
                continue

            coverage_ok = (
                vox_start is not None
                and vox_end is not None
                and evaluation.anchor_at >= vox_start
                and cutoff <= vox_end
            )

            if not coverage_ok:
                reason = (
                    "call_coverage_missing_for_attention"
                )
                blocked_reasons[reason] += 1
                rows.append(
                    StageSlaDealTruth(
                        deal_id=deal_id,
                        stage_id=stage_id,
                        stage_label=label,
                        status="BLOCKED",
                        manager_id=manager_id,
                        manager_name=manager_name,
                        stage_entered_at=entry,
                        anchor_at=evaluation.anchor_at,
                        deadline_at=evaluation.deadline_at,
                        last_qualifying_activity_at=(
                            last_activity_at
                        ),
                        last_qualifying_activity_kind=(
                            last_activity_kind
                        ),
                        blocker_reason=reason,
                    )
                )
                continue

            ambiguous_reset = any(
                evaluation.anchor_at
                <= call_at
                <= cutoff
                for call_at in calls.get(
                    deal_id,
                    [],
                )
            )

            if ambiguous_reset:
                reason = (
                    "successful_call_exact_reset_missing"
                )
                blocked_reasons[reason] += 1
                rows.append(
                    StageSlaDealTruth(
                        deal_id=deal_id,
                        stage_id=stage_id,
                        stage_label=label,
                        status="BLOCKED",
                        manager_id=manager_id,
                        manager_name=manager_name,
                        stage_entered_at=entry,
                        anchor_at=evaluation.anchor_at,
                        deadline_at=evaluation.deadline_at,
                        last_qualifying_activity_at=(
                            last_activity_at
                        ),
                        last_qualifying_activity_kind=(
                            last_activity_kind
                        ),
                        blocker_reason=reason,
                    )
                )
                continue

            manager_attention[
                (manager_id, manager_name)
            ] += 1

            rows.append(
                StageSlaDealTruth(
                    deal_id=deal_id,
                    stage_id=stage_id,
                    stage_label=label,
                    status="ATTENTION",
                    manager_id=manager_id,
                    manager_name=manager_name,
                    stage_entered_at=entry,
                    anchor_at=evaluation.anchor_at,
                    deadline_at=evaluation.deadline_at,
                    last_qualifying_activity_at=(
                        last_activity_at
                    ),
                    last_qualifying_activity_kind=(
                        last_activity_kind
                    ),
                    blocker_reason="",
                )
            )

        status_counts = Counter(
            item.status
            for item in rows
        )

        by_stage: list[
            tuple[str, int, int, int, int]
        ] = []

        for stage_id in TRACKED_STAGE_IDS:
            stage_rows = [
                item
                for item in rows
                if item.stage_id == stage_id
            ]
            if not stage_rows:
                continue

            stage_counter = Counter(
                item.status
                for item in stage_rows
            )

            by_stage.append(
                (
                    stage_id,
                    len(stage_rows),
                    stage_counter["OPEN"],
                    stage_counter["ATTENTION"],
                    stage_counter["BLOCKED"],
                )
            )

        sorted_rows = tuple(
            sorted(
                rows,
                key=lambda item: (
                    0
                    if item.status == "ATTENTION"
                    else 1
                    if item.status == "OPEN"
                    else 2,
                    item.stage_id,
                    item.manager_name,
                    item.deal_id,
                ),
            )
        )

        attention_by_manager = tuple(
            (
                manager_id,
                manager_name,
                count,
            )
            for (
                (manager_id, manager_name),
                count,
            ) in manager_attention.most_common()
        )

        return B2CStageSlaTruth(
            cutoff_at=cutoff,
            crm_run_id=crm_run_id,
            openlines_last_at=openlines_last,
            vox_run_id=vox_run_id,
            vox_window_start=vox_start,
            vox_window_end=vox_end,
            tracked_deals=len(rows),
            open=status_counts["OPEN"],
            attention=status_counts["ATTENTION"],
            blocked=status_counts["BLOCKED"],
            by_stage=tuple(by_stage),
            blocked_reasons=tuple(
                blocked_reasons.most_common()
            ),
            attention_by_manager=(
                attention_by_manager
            ),
            deals=sorted_rows,
        )
    finally:
        connection.close()
