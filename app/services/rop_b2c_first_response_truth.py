from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
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

        # A direct lead link is the strongest OpenLines evidence and must
        # preserve the legacy lead-level result. Secondary CRM links are
        # considered only when the chat has no direct lead link.
        resolved_chat_leads: dict[
            str,
            set[int],
        ] = {}

        ambiguous_chat_leads: dict[
            str,
            set[int],
        ] = {}

        if required_openlines.issubset(
            tables
        ):
            entity_to_leads: defaultdict[
                tuple[str, str],
                set[int],
            ] = defaultdict(set)

            for lead_id in b2c_leads:
                entity_to_leads[
                    ("lead", str(lead_id))
                ].add(lead_id)

            deal_link_rows = connection.execute(
                """
                SELECT entity_id, payload_json
                FROM crm_active_entities
                WHERE entity_type = 'deal'
                """
            ).fetchall()

            for deal_row in deal_link_rows:
                try:
                    deal_payload = json.loads(
                        deal_row["payload_json"]
                    )
                except (
                    TypeError,
                    json.JSONDecodeError,
                ):
                    continue

                if not isinstance(
                    deal_payload,
                    dict,
                ):
                    continue

                try:
                    lead_id = int(
                        str(
                            deal_payload.get(
                                "LEAD_ID"
                            )
                            or ""
                        )
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    continue

                if lead_id not in b2c_leads:
                    continue

                deal_id = str(
                    deal_row["entity_id"]
                ).strip()

                if deal_id:
                    entity_to_leads[
                        ("deal", deal_id)
                    ].add(lead_id)

                for key in (
                    "CONTACT_ID",
                    "CONTACT_IDS",
                ):
                    raw_contacts = (
                        deal_payload.get(key)
                    )

                    if raw_contacts in (
                        None,
                        "",
                    ):
                        continue

                    if isinstance(
                        raw_contacts,
                        (
                            list,
                            tuple,
                            set,
                        ),
                    ):
                        contacts = raw_contacts
                    else:
                        contacts = (
                            raw_contacts,
                        )

                    for raw_contact in contacts:
                        contact_id = str(
                            raw_contact
                            or ""
                        ).strip()

                        if contact_id:
                            entity_to_leads[
                                (
                                    "contact",
                                    contact_id,
                                )
                            ].add(
                                lead_id
                            )

                company_id = str(
                    deal_payload.get(
                        "COMPANY_ID"
                    )
                    or ""
                ).strip()

                if company_id:
                    entity_to_leads[
                        (
                            "company",
                            company_id,
                        )
                    ].add(
                        lead_id
                    )

            chat_buckets: defaultdict[
                str,
                defaultdict[
                    str,
                    set[int],
                ],
            ] = defaultdict(
                lambda: defaultdict(set)
            )

            link_rows = connection.execute(
                """
                SELECT
                    chat_id,
                    entity_type,
                    entity_id
                FROM openlines_crm_links
                """
            ).fetchall()

            for link_row in link_rows:
                chat_id = str(
                    link_row["chat_id"]
                ).strip()
                entity_type = str(
                    link_row["entity_type"]
                    or ""
                ).strip().lower()
                entity_id = str(
                    link_row["entity_id"]
                    or ""
                ).strip()

                if (
                    not chat_id
                    or entity_type
                    not in {
                        "lead",
                        "deal",
                        "contact",
                        "company",
                    }
                    or not entity_id
                ):
                    continue

                candidates = (
                    entity_to_leads.get(
                        (
                            entity_type,
                            entity_id,
                        ),
                        set(),
                    )
                )

                if candidates:
                    chat_buckets[
                        chat_id
                    ][
                        entity_type
                    ].update(
                        candidates
                    )

            for (
                chat_id,
                buckets,
            ) in chat_buckets.items():
                direct_leads = set(
                    buckets.get(
                        "lead",
                        set(),
                    )
                )

                if direct_leads:
                    # Preserve every direct lead relation exactly as the
                    # previous lead-only JOIN did. A deal/contact/company
                    # relation is not allowed to steal or downgrade it.
                    resolved_chat_leads[
                        chat_id
                    ] = direct_leads
                    continue

                secondary_leads: set[int] = set()

                for entity_type in (
                    "deal",
                    "contact",
                    "company",
                ):
                    secondary_leads.update(
                        buckets.get(
                            entity_type,
                            set(),
                        )
                    )

                if len(secondary_leads) == 1:
                    resolved_chat_leads[
                        chat_id
                    ] = secondary_leads
                elif secondary_leads:
                    # Conflicting secondary links are evidence that a
                    # response may exist, but not proof for one lead.
                    ambiguous_chat_leads[
                        chat_id
                    ] = secondary_leads

            message_rows = connection.execute(
                """
                SELECT
                    chat_id,
                    message_id,
                    sent_at,
                    sender_directory_user_id
                FROM openlines_messages
                WHERE sender_role = 'manager'
                  AND sender_directory_user_id
                      IS NOT NULL
                  AND sent_at IS NOT NULL
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

        ambiguous_messages_by_lead: defaultdict[
            int,
            list[datetime],
        ] = defaultdict(list)

        for row in message_rows:
            chat_id = str(
                row["chat_id"]
            )

            sent = _dt(
                row["sent_at"]
            )

            if sent is None:
                continue

            for lead_id in resolved_chat_leads.get(
                chat_id,
                set(),
            ):
                lead = b2c_leads.get(
                    lead_id
                )

                if lead is None:
                    continue

                created = lead[
                    "created"
                ]

                if (
                    not isinstance(
                        created,
                        datetime,
                    )
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

            for lead_id in ambiguous_chat_leads.get(
                chat_id,
                set(),
            ):
                lead = b2c_leads.get(
                    lead_id
                )

                if lead is None:
                    continue

                created = lead[
                    "created"
                ]

                if (
                    isinstance(
                        created,
                        datetime,
                    )
                    and sent >= created
                ):
                    ambiguous_messages_by_lead[
                        lead_id
                    ].append(sent)

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

        owner_type_to_entity = {
            "1": "LEAD",
            "2": "DEAL",
            "3": "CONTACT",
            "4": "COMPANY",
        }

        deal_to_lead: dict[str, int] = {}
        contact_to_leads: defaultdict[str, set[int]] = defaultdict(set)
        company_to_leads: defaultdict[str, set[int]] = defaultdict(set)

        deal_rows = connection.execute(
            """
            SELECT entity_id, payload_json
            FROM crm_active_entities
            WHERE entity_type = 'deal'
            """
        ).fetchall()

        for row in deal_rows:
            try:
                deal = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue

            if not isinstance(deal, dict):
                continue

            try:
                lead_id = int(str(deal.get("LEAD_ID") or ""))
            except (TypeError, ValueError):
                continue

            if lead_id not in b2c_leads:
                continue

            deal_id = str(row["entity_id"])
            deal_to_lead[deal_id] = lead_id

            for key in ("CONTACT_ID", "CONTACT_IDS"):
                raw_contacts = deal.get(key)

                if raw_contacts in (None, ""):
                    continue

                if isinstance(raw_contacts, (list, tuple, set)):
                    contacts = raw_contacts
                else:
                    contacts = (raw_contacts,)

                for raw_contact in contacts:
                    contact_id = str(raw_contact or "").strip()

                    if contact_id:
                        contact_to_leads[contact_id].add(lead_id)

            company_id = str(deal.get("COMPANY_ID") or "").strip()

            if company_id:
                company_to_leads[company_id].add(lead_id)

        activity_owner: dict[str, tuple[str, str]] = {}

        activity_rows = connection.execute(
            """
            SELECT
                entity_id,
                CAST(
                    json_extract(
                        payload_json,
                        '$.OWNER_TYPE_ID'
                    ) AS TEXT
                ) AS owner_type_id,
                CAST(
                    json_extract(
                        payload_json,
                        '$.OWNER_ID'
                    ) AS TEXT
                ) AS owner_id
            FROM crm_active_entities
            WHERE entity_type = 'activity'
            """
        ).fetchall()

        for row in activity_rows:
            entity_type = owner_type_to_entity.get(
                str(row["owner_type_id"] or "")
            )
            entity_id = str(row["owner_id"] or "").strip()

            if entity_type and entity_id:
                activity_owner[str(row["entity_id"])] = (
                    entity_type,
                    entity_id,
                )

        def candidate_leads(
            entity_type: str,
            entity_id: str,
        ) -> set[int]:
            entity_type = entity_type.strip().upper()
            entity_id = entity_id.strip()

            if not entity_type or not entity_id:
                return set()

            if entity_type == "LEAD":
                try:
                    lead_id = int(entity_id)
                except (TypeError, ValueError):
                    return set()

                return {lead_id} if lead_id in b2c_leads else set()

            if entity_type == "DEAL":
                lead_id = deal_to_lead.get(entity_id)
                return {lead_id} if lead_id is not None else set()

            if entity_type == "CONTACT":
                return set(
                    contact_to_leads.get(
                        entity_id,
                        set(),
                    )
                )

            if entity_type == "COMPANY":
                return set(
                    company_to_leads.get(
                        entity_id,
                        set(),
                    )
                )

            return set()

        calls_by_lead: dict[int, list[datetime]] = {}

        if (
            vox_run_id is not None
            and "rop_voximplant_statistic_facts" in tables
        ):
            call_rows = connection.execute(
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
                (vox_run_id,),
            ).fetchall()

            for row in call_rows:
                candidates: set[int] = set()

                candidates.update(
                    candidate_leads(
                        str(row["crm_entity_type"] or ""),
                        str(row["crm_entity_id"] or ""),
                    )
                )

                activity_key = activity_owner.get(
                    str(row["crm_activity_id"] or "")
                )

                if activity_key is not None:
                    candidates.update(
                        candidate_leads(
                            activity_key[0],
                            activity_key[1],
                        )
                    )

                if not candidates:
                    continue

                started = _dt(row["call_start_at"])

                if started is None:
                    continue

                # Conservative evidence rule:
                # if one successful call can plausibly belong to several
                # B2C leads, protect every candidate from a false breach.
                for lead_id in candidates:
                    calls_by_lead.setdefault(
                        lead_id,
                        [],
                    ).append(started)

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

                ambiguous_message = any(
                    created
                    <= message_at
                    <= response_at
                    for message_at in
                    ambiguous_messages_by_lead.get(
                        lead_id,
                        [],
                    )
                )

                if ambiguous_message:
                    result["blocked"] += 1

                    blockers[
                        "openlines_crm_link_ambiguous"
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

            ambiguous_message = any(
                created
                <= message_at
                <= vox_end
                for message_at in
                ambiguous_messages_by_lead.get(
                    lead_id,
                    [],
                )
            )

            if ambiguous_message:
                result["blocked"] += 1

                blockers[
                    "openlines_crm_link_ambiguous"
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
