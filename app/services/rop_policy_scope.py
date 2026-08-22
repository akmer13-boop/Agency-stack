from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.rop_policy_engine import (
    load_policy_contract,
)
from app.storage.rop_lead_policy_profile_store import (
    TOURISM_B2C_PROFILE,
    RopLeadPolicyProfileStore,
)

B2C_DEPARTMENT_ID = "19"

EXPLICIT_LEAD_EXCLUSIONS = (
    ("36", "concierge"),
    ("41", "concierge"),
    ("44", "concierge"),
    ("20", "b2b"),
    ("24", "russian_tour"),
)

CONCIERGE_DEPARTMENT_IDS = frozenset(
    {
        "36",
        "41",
        "44",
    }
)


@dataclass(frozen=True, slots=True)
class PolicyScopeDecision:
    entity_type: str
    entity_id: int
    profile_key: str
    eligible: bool
    reason: str
    category_id: str = ""
    department_ids: tuple[str, ...] = ()
    evidence_kind: str = ""


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
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None

    return (
        parsed
        if isinstance(
            parsed,
            dict,
        )
        else None
    )


def _entity_payload(
    database_path: str,
    *,
    entity_type: str,
    entity_id: int,
) -> dict[str, Any] | None:
    connection = _connect(database_path)

    try:
        source = _crm_source(_objects(connection))

        if source is None:
            return None

        row = connection.execute(
            f"""
            SELECT payload_json
            FROM {source}
            WHERE entity_type = ?
              AND entity_id = ?
            LIMIT 1
            """,
            (
                entity_type,
                str(entity_id),
            ),
        ).fetchone()

        if row is None:
            return None

        return _payload(row["payload_json"])

    finally:
        connection.close()


def _department_ids(
    value: Any,
) -> tuple[str, ...]:
    if value in (
        None,
        "",
    ):
        return ()

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return tuple(
            sorted(
                {
                    str(item).strip()
                    for item in value
                    if item
                    not in (
                        None,
                        "",
                    )
                }
            )
        )

    return (str(value).strip(),)


def _assigned_department_ids(
    database_path: str,
    payload: dict[str, Any],
) -> tuple[str, ...]:
    assigned = payload.get("ASSIGNED_BY_ID") or payload.get("RESPONSIBLE_ID")

    if assigned in (
        None,
        "",
    ):
        return ()

    user = _entity_payload(
        database_path,
        entity_type="user",
        entity_id=int(str(assigned)),
    )

    if user is None:
        return ()

    return _department_ids(user.get("UF_DEPARTMENT"))


def _tourism_category_id() -> str:
    contract = load_policy_contract()

    funnel = contract.binding.get("business_policy_funnel")

    if not isinstance(
        funnel,
        dict,
    ):
        return ""

    return str(funnel.get("category_id") or "").strip()


def _linked_deals(
    database_path: str,
    lead_id: int,
) -> tuple[tuple[int, str], ...]:
    connection = _connect(database_path)

    try:
        source = _crm_source(_objects(connection))

        if source is None:
            return ()

        rows = connection.execute(
            f"""
            SELECT
                entity_id,
                payload_json
            FROM {source}
            WHERE entity_type = 'deal'
              AND json_valid(
                    payload_json
                  )
              AND CAST(
                    json_extract(
                        payload_json,
                        '$.LEAD_ID'
                    )
                    AS TEXT
                  ) = ?
            ORDER BY
                CAST(
                    entity_id
                    AS INTEGER
                )
            """,
            (str(lead_id),),
        ).fetchall()

        result: list[tuple[int, str]] = []

        for row in rows:
            item = _payload(row["payload_json"])

            if item is None:
                continue

            raw_category = item.get("CATEGORY_ID")

            if raw_category in (
                None,
                "",
            ):
                continue

            try:
                deal_id = int(row["entity_id"])
            except (
                TypeError,
                ValueError,
            ):
                continue

            result.append(
                (
                    deal_id,
                    str(raw_category).strip(),
                )
            )

        return tuple(result)

    finally:
        connection.close()


def _lead_exclusion(
    department_ids: tuple[str, ...],
) -> (
    tuple[
        str,
        str,
    ]
    | None
):
    departments = set(department_ids)

    for (
        department_id,
        label,
    ) in EXPLICIT_LEAD_EXCLUSIONS:
        if department_id in departments:
            return (
                department_id,
                label,
            )

    return None


def _resolve_lead_scope(
    database_path: str,
    lead_id: int,
) -> PolicyScopeDecision:
    lead = _entity_payload(
        database_path,
        entity_type="lead",
        entity_id=lead_id,
    )

    if lead is None:
        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=lead_id,
            profile_key="unresolved",
            eligible=False,
            reason="lead_policy_profile_unresolved",
        )

    departments = _assigned_department_ids(
        database_path,
        lead,
    )

    exclusion = _lead_exclusion(departments)

    if exclusion is not None:
        (
            department_id,
            label,
        ) = exclusion

        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=lead_id,
            profile_key="excluded",
            eligible=False,
            reason=("lead_department_excluded:" + label),
            department_ids=departments,
            evidence_kind=("department:" + department_id),
        )

    linked = _linked_deals(
        database_path,
        lead_id,
    )

    tourism_category = _tourism_category_id()

    categories = {
        category_id
        for (
            _deal_id,
            category_id,
        ) in linked
    }

    if tourism_category and tourism_category in categories and len(categories) > 1:
        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=lead_id,
            profile_key="unresolved",
            eligible=False,
            reason=("lead_linked_multiple_funnels"),
            category_id=(tourism_category),
            department_ids=departments,
            evidence_kind=("deal_link_conflict"),
        )

    if tourism_category and categories == {
        tourism_category,
    }:
        deal_id = next(
            deal_id
            for (
                deal_id,
                category_id,
            ) in linked
            if (category_id == tourism_category)
        )

        RopLeadPolicyProfileStore(database_path).confirm_tourism_b2c(
            lead_id=lead_id,
            evidence_kind=("linked_b2c_deal"),
            evidence_ref=("deal:" + str(deal_id) + ":category:" + tourism_category),
        )

        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=lead_id,
            profile_key=(TOURISM_B2C_PROFILE),
            eligible=True,
            reason=("lead_linked_b2c_deal"),
            category_id=(tourism_category),
            department_ids=departments,
            evidence_kind=("linked_b2c_deal"),
        )

    if categories:
        first_category = sorted(categories)[0]

        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=lead_id,
            profile_key="unbound",
            eligible=False,
            reason=("lead_linked_non_b2c_deal"),
            category_id=(first_category),
            department_ids=departments,
            evidence_kind=("linked_non_b2c_deal"),
        )

    if B2C_DEPARTMENT_ID in set(departments):
        RopLeadPolicyProfileStore(database_path).confirm_tourism_b2c(
            lead_id=lead_id,
            evidence_kind=("b2c_department"),
            evidence_ref=("department:" + B2C_DEPARTMENT_ID),
        )

        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=lead_id,
            profile_key=(TOURISM_B2C_PROFILE),
            eligible=True,
            reason=("lead_b2c_department_match"),
            department_ids=departments,
            evidence_kind=("b2c_department"),
        )

    sticky = RopLeadPolicyProfileStore(database_path).get(lead_id)

    if sticky is not None and sticky.profile_key == TOURISM_B2C_PROFILE:
        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=lead_id,
            profile_key=(TOURISM_B2C_PROFILE),
            eligible=True,
            reason=("lead_b2c_profile_sticky"),
            department_ids=departments,
            evidence_kind=(sticky.evidence_kind),
        )

    return PolicyScopeDecision(
        entity_type="lead",
        entity_id=lead_id,
        profile_key="unresolved",
        eligible=False,
        reason=("lead_policy_profile_unresolved"),
        department_ids=departments,
    )


def _resolve_deal_scope(
    database_path: str,
    deal_id: int,
) -> PolicyScopeDecision:
    deal = _entity_payload(
        database_path,
        entity_type="deal",
        entity_id=deal_id,
    )

    if deal is None:
        return PolicyScopeDecision(
            entity_type="deal",
            entity_id=deal_id,
            profile_key="unresolved",
            eligible=False,
            reason="deal_not_found",
        )

    departments = _assigned_department_ids(
        database_path,
        deal,
    )

    if set(departments) & CONCIERGE_DEPARTMENT_IDS:
        return PolicyScopeDecision(
            entity_type="deal",
            entity_id=deal_id,
            profile_key="excluded",
            eligible=False,
            reason=("deal_department_excluded:concierge"),
            department_ids=departments,
            evidence_kind=("concierge_department"),
        )

    category_id = str(deal.get("CATEGORY_ID") or "").strip()

    if not category_id:
        return PolicyScopeDecision(
            entity_type="deal",
            entity_id=deal_id,
            profile_key="unresolved",
            eligible=False,
            reason=("deal_category_missing"),
            department_ids=departments,
        )

    tourism_category = _tourism_category_id()

    if tourism_category and category_id == tourism_category:
        return PolicyScopeDecision(
            entity_type="deal",
            entity_id=deal_id,
            profile_key=(TOURISM_B2C_PROFILE),
            eligible=True,
            reason=("tourism_b2c_category_match"),
            category_id=category_id,
            department_ids=departments,
            evidence_kind=("deal_category"),
        )

    return PolicyScopeDecision(
        entity_type="deal",
        entity_id=deal_id,
        profile_key="unbound",
        eligible=False,
        reason=("deal_policy_profile_unbound:" + category_id),
        category_id=category_id,
        department_ids=departments,
    )


def _selected_payloads(
    connection: sqlite3.Connection,
    source: str,
    entity_type: str,
    entity_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    normalized = tuple(
        sorted(
            {
                str(entity_id).strip()
                for entity_id in entity_ids
                if str(entity_id).strip()
            }
        )
    )

    if not normalized:
        return {}

    result: dict[
        str,
        dict[str, Any],
    ] = {}

    for offset in range(
        0,
        len(normalized),
        500,
    ):
        chunk = normalized[
            offset : offset + 500
        ]
        placeholders = ",".join(
            "?" for _item in chunk
        )
        rows = connection.execute(
            f"""
            SELECT entity_id, payload_json
            FROM {source}
            WHERE entity_type = ?
              AND entity_id IN ({placeholders})
            """,
            (
                entity_type,
                *chunk,
            ),
        ).fetchall()

        for row in rows:
            item = _payload(
                row["payload_json"]
            )
            if item is not None:
                result[
                    str(row["entity_id"])
                ] = item

    return result


def resolve_lead_policy_scopes(
    database_path: str,
    lead_ids: Iterable[int],
) -> dict[int, PolicyScopeDecision]:
    normalized = tuple(
        sorted(
            {
                lead_id
                for lead_id in lead_ids
                if lead_id > 0
            }
        )
    )

    if not normalized:
        return {}

    profile_store = RopLeadPolicyProfileStore(
        database_path
    )
    sticky_profiles = profile_store.get_many(
        normalized
    )

    connection = _connect(database_path)

    try:
        source = _crm_source(
            _objects(connection)
        )

        if source is None:
            return {
                lead_id: PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key="unresolved",
                    eligible=False,
                    reason=(
                        "lead_policy_profile_unresolved"
                    ),
                )
                for lead_id in normalized
            }

        leads = _selected_payloads(
            connection,
            source,
            "lead",
            (
                str(lead_id)
                for lead_id in normalized
            ),
        )

        assigned_user_ids = {
            str(
                lead.get("ASSIGNED_BY_ID")
                or lead.get("RESPONSIBLE_ID")
                or ""
            ).strip()
            for lead in leads.values()
        }
        assigned_user_ids.discard("")

        users = _selected_payloads(
            connection,
            source,
            "user",
            assigned_user_ids,
        )

        linked_deals: dict[
            int,
            list[tuple[int, str]],
        ] = {
            lead_id: []
            for lead_id in normalized
        }
        normalized_set = set(normalized)

        deal_rows = connection.execute(
            f"""
            SELECT entity_id, payload_json
            FROM {source}
            WHERE entity_type = 'deal'
            """
        ).fetchall()

        for row in deal_rows:
            item = _payload(
                row["payload_json"]
            )
            if item is None:
                continue

            try:
                lead_id = int(
                    str(
                        item.get("LEAD_ID")
                        or ""
                    )
                )
                deal_id = int(
                    row["entity_id"]
                )
            except (
                TypeError,
                ValueError,
            ):
                continue

            raw_category = item.get(
                "CATEGORY_ID"
            )

            if (
                lead_id not in normalized_set
                or raw_category in (
                    None,
                    "",
                )
            ):
                continue

            linked_deals[lead_id].append(
                (
                    deal_id,
                    str(raw_category).strip(),
                )
            )

        for deals in linked_deals.values():
            deals.sort(
                key=lambda item: item[0]
            )

    finally:
        connection.close()

    tourism_category = _tourism_category_id()
    confirmations: list[
        tuple[int, str, str]
    ] = []
    decisions: dict[
        int,
        PolicyScopeDecision,
    ] = {}

    for lead_id in normalized:
        lead = leads.get(
            str(lead_id)
        )

        if lead is None:
            decisions[lead_id] = (
                PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key="unresolved",
                    eligible=False,
                    reason=(
                        "lead_policy_profile_unresolved"
                    ),
                )
            )
            continue

        assigned = str(
            lead.get("ASSIGNED_BY_ID")
            or lead.get("RESPONSIBLE_ID")
            or ""
        ).strip()
        user = users.get(assigned)
        departments = _department_ids(
            user.get("UF_DEPARTMENT")
            if user is not None
            else None
        )
        exclusion = _lead_exclusion(
            departments
        )

        if exclusion is not None:
            (
                department_id,
                label,
            ) = exclusion
            decisions[lead_id] = (
                PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key="excluded",
                    eligible=False,
                    reason=(
                        "lead_department_excluded:"
                        + label
                    ),
                    department_ids=departments,
                    evidence_kind=(
                        "department:"
                        + department_id
                    ),
                )
            )
            continue

        linked = linked_deals[lead_id]
        categories = {
            category_id
            for _deal_id, category_id
            in linked
        }

        if (
            tourism_category
            and tourism_category in categories
            and len(categories) > 1
        ):
            decisions[lead_id] = (
                PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key="unresolved",
                    eligible=False,
                    reason=(
                        "lead_linked_multiple_funnels"
                    ),
                    category_id=(
                        tourism_category
                    ),
                    department_ids=departments,
                    evidence_kind=(
                        "deal_link_conflict"
                    ),
                )
            )
            continue

        if (
            tourism_category
            and categories == {
                tourism_category,
            }
        ):
            deal_id = next(
                deal_id
                for deal_id, category_id
                in linked
                if category_id
                == tourism_category
            )
            evidence_ref = (
                "deal:"
                + str(deal_id)
                + ":category:"
                + tourism_category
            )
            confirmations.append(
                (
                    lead_id,
                    "linked_b2c_deal",
                    evidence_ref,
                )
            )
            decisions[lead_id] = (
                PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key=(
                        TOURISM_B2C_PROFILE
                    ),
                    eligible=True,
                    reason=(
                        "lead_linked_b2c_deal"
                    ),
                    category_id=(
                        tourism_category
                    ),
                    department_ids=departments,
                    evidence_kind=(
                        "linked_b2c_deal"
                    ),
                )
            )
            continue

        if categories:
            first_category = sorted(
                categories
            )[0]
            decisions[lead_id] = (
                PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key="unbound",
                    eligible=False,
                    reason=(
                        "lead_linked_non_b2c_deal"
                    ),
                    category_id=first_category,
                    department_ids=departments,
                    evidence_kind=(
                        "linked_non_b2c_deal"
                    ),
                )
            )
            continue

        if B2C_DEPARTMENT_ID in set(
            departments
        ):
            evidence_ref = (
                "department:"
                + B2C_DEPARTMENT_ID
            )
            confirmations.append(
                (
                    lead_id,
                    "b2c_department",
                    evidence_ref,
                )
            )
            decisions[lead_id] = (
                PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key=(
                        TOURISM_B2C_PROFILE
                    ),
                    eligible=True,
                    reason=(
                        "lead_b2c_department_match"
                    ),
                    department_ids=departments,
                    evidence_kind=(
                        "b2c_department"
                    ),
                )
            )
            continue

        sticky = sticky_profiles.get(
            lead_id
        )

        if (
            sticky is not None
            and sticky.profile_key
            == TOURISM_B2C_PROFILE
        ):
            decisions[lead_id] = (
                PolicyScopeDecision(
                    entity_type="lead",
                    entity_id=lead_id,
                    profile_key=(
                        TOURISM_B2C_PROFILE
                    ),
                    eligible=True,
                    reason=(
                        "lead_b2c_profile_sticky"
                    ),
                    department_ids=departments,
                    evidence_kind=(
                        sticky.evidence_kind
                    ),
                )
            )
            continue

        decisions[lead_id] = (
            PolicyScopeDecision(
                entity_type="lead",
                entity_id=lead_id,
                profile_key="unresolved",
                eligible=False,
                reason=(
                    "lead_policy_profile_unresolved"
                ),
                department_ids=departments,
            )
        )

    profile_store.confirm_many_tourism_b2c(
        confirmations
    )

    return decisions


def resolve_deal_policy_scopes(
    database_path: str,
    deal_ids: Iterable[int],
) -> dict[int, PolicyScopeDecision]:
    normalized = tuple(
        sorted(
            {
                deal_id
                for deal_id in deal_ids
                if deal_id > 0
            }
        )
    )

    if not normalized:
        return {}

    connection = _connect(database_path)

    try:
        source = _crm_source(
            _objects(connection)
        )

        if source is None:
            return {
                deal_id: PolicyScopeDecision(
                    entity_type="deal",
                    entity_id=deal_id,
                    profile_key="unresolved",
                    eligible=False,
                    reason="deal_not_found",
                )
                for deal_id in normalized
            }

        deals = _selected_payloads(
            connection,
            source,
            "deal",
            (
                str(deal_id)
                for deal_id in normalized
            ),
        )
        assigned_user_ids = {
            str(
                deal.get("ASSIGNED_BY_ID")
                or deal.get("RESPONSIBLE_ID")
                or ""
            ).strip()
            for deal in deals.values()
        }
        assigned_user_ids.discard("")
        users = _selected_payloads(
            connection,
            source,
            "user",
            assigned_user_ids,
        )

    finally:
        connection.close()

    tourism_category = _tourism_category_id()
    decisions: dict[
        int,
        PolicyScopeDecision,
    ] = {}

    for deal_id in normalized:
        deal = deals.get(str(deal_id))

        if deal is None:
            decisions[deal_id] = (
                PolicyScopeDecision(
                    entity_type="deal",
                    entity_id=deal_id,
                    profile_key="unresolved",
                    eligible=False,
                    reason="deal_not_found",
                )
            )
            continue

        assigned = str(
            deal.get("ASSIGNED_BY_ID")
            or deal.get("RESPONSIBLE_ID")
            or ""
        ).strip()
        user = users.get(assigned)
        departments = _department_ids(
            user.get("UF_DEPARTMENT")
            if user is not None
            else None
        )

        if set(departments) & CONCIERGE_DEPARTMENT_IDS:
            decisions[deal_id] = (
                PolicyScopeDecision(
                    entity_type="deal",
                    entity_id=deal_id,
                    profile_key="excluded",
                    eligible=False,
                    reason=(
                        "deal_department_excluded:concierge"
                    ),
                    department_ids=departments,
                    evidence_kind=(
                        "concierge_department"
                    ),
                )
            )
            continue

        category_id = str(
            deal.get("CATEGORY_ID")
            or ""
        ).strip()

        if not category_id:
            decisions[deal_id] = (
                PolicyScopeDecision(
                    entity_type="deal",
                    entity_id=deal_id,
                    profile_key="unresolved",
                    eligible=False,
                    reason="deal_category_missing",
                    department_ids=departments,
                )
            )
            continue

        if (
            tourism_category
            and category_id
            == tourism_category
        ):
            decisions[deal_id] = (
                PolicyScopeDecision(
                    entity_type="deal",
                    entity_id=deal_id,
                    profile_key=(
                        TOURISM_B2C_PROFILE
                    ),
                    eligible=True,
                    reason=(
                        "tourism_b2c_category_match"
                    ),
                    category_id=category_id,
                    department_ids=departments,
                    evidence_kind="deal_category",
                )
            )
            continue

        decisions[deal_id] = (
            PolicyScopeDecision(
                entity_type="deal",
                entity_id=deal_id,
                profile_key="unbound",
                eligible=False,
                reason=(
                    "deal_policy_profile_unbound:"
                    + category_id
                ),
                category_id=category_id,
                department_ids=departments,
            )
        )

    return decisions


def resolve_policy_scope(
    database_path: str,
    *,
    entity_type: str,
    entity_id: int,
) -> PolicyScopeDecision:
    if entity_id <= 0:
        return PolicyScopeDecision(
            entity_type=entity_type,
            entity_id=entity_id,
            profile_key="unresolved",
            eligible=False,
            reason=("policy_scope_entity_id_invalid"),
        )

    if entity_type == "lead":
        return _resolve_lead_scope(
            database_path,
            entity_id,
        )

    if entity_type == "deal":
        return _resolve_deal_scope(
            database_path,
            entity_id,
        )

    return PolicyScopeDecision(
        entity_type=entity_type,
        entity_id=entity_id,
        profile_key="unresolved",
        eligible=False,
        reason=("entity_type_has_no_policy_profile"),
    )
