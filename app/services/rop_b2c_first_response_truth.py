from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.rop_business_time import (
    TimerStatus,
    evaluate_first_response,
)
from app.services.rop_policy_scope import (
    resolve_policy_scope,
)


@dataclass(frozen=True, slots=True)
class B2CFirstResponseTruth:
    window_start: datetime
    window_end: datetime
    all_leads_created: int
    b2c_proven: int
    excluded_or_out_of_scope: int
    unresolved: int
    measured: int
    ok: int
    breach: int
    open: int
    blocked: int
    blocked_reasons: tuple[tuple[str, int], ...]
    breach_by_manager: tuple[tuple[str, int], ...]
    unattributed_breaches: int
    vox_run_id: int | None
    vox_window_start: datetime | None
    vox_window_end: datetime | None

    @property
    def closed_measured(self) -> int:
        return self.ok + self.breach

    @property
    def measured_share_percent(self) -> float:
        if self.b2c_proven <= 0:
            return 0.0

        return (
            100.0
            * self.measured
            / self.b2c_proven
        )

    @property
    def ok_share_closed_percent(self) -> float:
        if self.closed_measured <= 0:
            return 0.0

        return (
            100.0
            * self.ok
            / self.closed_measured
        )


def _dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None

    try:
        result = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=UTC
        )

    return result.astimezone(UTC)


def _connect(
    database_path: str,
) -> sqlite3.Connection:
    path = Path(
        database_path
    ).resolve()

    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA query_only=ON"
    )

    return connection


def build_b2c_first_response_truth(
    database_path: str,
    *,
    now: datetime | None = None,
) -> B2CFirstResponseTruth:
    observed_at = (
        now or datetime.now(UTC)
    ).astimezone(UTC)

    moscow = ZoneInfo(
        "Europe/Moscow"
    )

    local_now = observed_at.astimezone(
        moscow
    )

    month_start = (
        local_now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        .astimezone(UTC)
    )

    connection = _connect(
        database_path
    )

    try:
        rows = connection.execute(
            """
            SELECT
                entity_id,
                payload_json
            FROM crm_active_entities
            WHERE entity_type = 'lead'
            ORDER BY CAST(
                entity_id AS INTEGER
            )
            """
        ).fetchall()

        leads: dict[
            int,
            dict[str, object],
        ] = {}

        for row in rows:
            try:
                payload = json.loads(
                    row["payload_json"]
                )
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

            created = _dt(
                payload.get(
                    "DATE_CREATE"
                )
            )

            if created is None:
                continue

            if not (
                month_start
                <= created
                <= observed_at
            ):
                continue

            leads[
                int(row["entity_id"])
            ] = {
                "created": created,
            }

        scope = Counter()

        b2c_leads: dict[
            int,
            dict[str, object],
        ] = {}

        for (
            lead_id,
            lead,
        ) in leads.items():
            decision = resolve_policy_scope(
                database_path,
                entity_type="lead",
                entity_id=lead_id,
            )

            if decision.eligible:
                scope["b2c"] += 1

                b2c_leads[
                    lead_id
                ] = lead

            elif decision.profile_key in {
                "excluded",
                "unbound",
            }:
                scope["out"] += 1

            else:
                scope["unresolved"] += 1

        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }

        required_openlines = {
            "openlines_crm_links",
            "openlines_messages",
        }

        if required_openlines.issubset(
            tables
        ):
            message_rows = connection.execute(
                """
                SELECT
                    link.entity_id AS lead_id,
                    msg.message_id,
                    msg.sent_at,
                    msg.sender_directory_user_id
                FROM openlines_crm_links AS link
                JOIN openlines_messages AS msg
                  ON msg.chat_id = link.chat_id
                WHERE link.entity_type = 'lead'
                  AND msg.sender_role = 'manager'
                  AND
                      msg.sender_directory_user_id
                      IS NOT NULL
                  AND msg.sent_at IS NOT NULL
                """
            ).fetchall()
        else:
            message_rows = []

        first_message: dict[
            int,
            tuple[
                datetime,
                str,
            ],
        ] = {}

        for row in message_rows:
            try:
                lead_id = int(
                    row["lead_id"]
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            lead = b2c_leads.get(
                lead_id
            )

            if lead is None:
                continue

            sent = _dt(
                row["sent_at"]
            )

            created = lead[
                "created"
            ]

            if not isinstance(
                created,
                datetime,
            ):
                continue

            if (
                sent is None
                or sent < created
            ):
                continue

            current = first_message.get(
                lead_id
            )

            if (
                current is None
                or sent < current[0]
            ):
                first_message[
                    lead_id
                ] = (
                    sent,
                    str(
                        row[
                            "sender_directory_user_id"
                        ]
                    ),
                )

        vox_run_id = None
        vox_start = None
        vox_end = None

        if (
            "rop_voximplant_reconciliation_runs"
            in tables
        ):
            row = connection.execute(
                """
                SELECT
                    id,
                    window_start,
                    window_end
                FROM
                    rop_voximplant_reconciliation_runs
                WHERE pagination_complete = 1
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

            if row is not None:
                vox_run_id = int(
                    row["id"]
                )

                vox_start = _dt(
                    row["window_start"]
                )

                vox_end = _dt(
                    row["window_end"]
                )

        activity_to_lead: dict[
            str,
            int,
        ] = {}

        activity_rows = connection.execute(
            """
            SELECT
                entity_id,
                CAST(
                    json_extract(
                        payload_json,
                        '$.OWNER_ID'
                    )
                    AS TEXT
                ) AS owner_id
            FROM crm_active_entities
            WHERE entity_type = 'activity'
              AND CAST(
                    json_extract(
                        payload_json,
                        '$.OWNER_TYPE_ID'
                    )
                    AS TEXT
                  ) = '1'
            """
        ).fetchall()

        for row in activity_rows:
            owner_id = str(
                row["owner_id"]
                or ""
            )

            if owner_id.isdigit():
                activity_to_lead[
                    str(
                        row[
                            "entity_id"
                        ]
                    )
                ] = int(
                    owner_id
                )

        calls_by_lead: dict[
            int,
            list[datetime],
        ] = {}

        if (
            vox_run_id is not None
            and
            "rop_voximplant_statistic_facts"
            in tables
        ):
            call_rows = connection.execute(
                """
                SELECT
                    call_start_at,
                    crm_activity_id
                FROM
                    rop_voximplant_statistic_facts
                WHERE last_seen_run_id = ?
                  AND call_failed_code = '200'
                """,
                (
                    vox_run_id,
                ),
            ).fetchall()

            for row in call_rows:
                lead_id = activity_to_lead.get(
                    str(
                        row[
                            "crm_activity_id"
                        ]
                        or ""
                    )
                )

                if lead_id is None:
                    continue

                started = _dt(
                    row["call_start_at"]
                )

                if started is None:
                    continue

                calls_by_lead.setdefault(
                    lead_id,
                    [],
                ).append(
                    started
                )

        result = Counter()

        blockers = Counter()

        breach_managers = Counter()

        unattributed_breaches = 0

        for (
            lead_id,
            lead,
        ) in b2c_leads.items():
            created = lead[
                "created"
            ]

            if not isinstance(
                created,
                datetime,
            ):
                continue

            exact = first_message.get(
                lead_id
            )

            if exact is not None:
                (
                    response_at,
                    manager_id,
                ) = exact

                evaluation = (
                    evaluate_first_response(
                        lead_created_at=created,
                        response_at=response_at,
                    )
                )

                if (
                    evaluation.status
                    is TimerStatus.OK
                ):
                    result["ok"] += 1
                    continue

                coverage_full = (
                    vox_start is not None
                    and vox_end is not None
                    and created >= vox_start
                    and response_at <= vox_end
                )

                if not coverage_full:
                    result["blocked"] += 1

                    blockers[
                        "call_coverage_missing_for_breach"
                    ] += 1

                    continue

                ambiguous_call = any(
                    created
                    <= call_start
                    <= response_at
                    for call_start
                    in calls_by_lead.get(
                        lead_id,
                        [],
                    )
                )

                if ambiguous_call:
                    result["blocked"] += 1

                    blockers[
                        "successful_call_exact_answer_missing"
                    ] += 1

                    continue

                result["breach"] += 1

                breach_managers[
                    manager_id
                ] += 1

                continue

            coverage_full = (
                vox_start is not None
                and vox_end is not None
                and created >= vox_start
                and created <= vox_end
            )

            if not coverage_full:
                result["blocked"] += 1

                blockers[
                    "call_coverage_missing_no_message"
                ] += 1

                continue

            ambiguous_call = any(
                created
                <= call_start
                <= vox_end
                for call_start
                in calls_by_lead.get(
                    lead_id,
                    [],
                )
            )

            if ambiguous_call:
                result["blocked"] += 1

                blockers[
                    "successful_call_exact_answer_missing"
                ] += 1

                continue

            evaluation = (
                evaluate_first_response(
                    lead_created_at=created,
                    as_of=vox_end,
                )
            )

            if (
                evaluation.status
                is TimerStatus.BREACH
            ):
                result["breach"] += 1

                # No exact response actor exists here.
                # Do not blame the current assignee.
                unattributed_breaches += 1

            else:
                result["open"] += 1

        measured = (
            result["ok"]
            + result["breach"]
            + result["open"]
        )

        return B2CFirstResponseTruth(
            window_start=month_start,
            window_end=observed_at,
            all_leads_created=len(
                leads
            ),
            b2c_proven=scope["b2c"],
            excluded_or_out_of_scope=(
                scope["out"]
            ),
            unresolved=(
                scope["unresolved"]
            ),
            measured=measured,
            ok=result["ok"],
            breach=result["breach"],
            open=result["open"],
            blocked=result["blocked"],
            blocked_reasons=tuple(
                blockers.most_common()
            ),
            breach_by_manager=tuple(
                breach_managers.most_common()
            ),
            unattributed_breaches=(
                unattributed_breaches
            ),
            vox_run_id=vox_run_id,
            vox_window_start=vox_start,
            vox_window_end=vox_end,
        )

    finally:
        connection.close()
