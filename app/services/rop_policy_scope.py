from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.rop_policy_engine import (
    load_policy_contract,
)

TOURISM_B2C_PROFILE = "tourism_b2c"


@dataclass(frozen=True, slots=True)
class PolicyScopeDecision:
    entity_type: str
    entity_id: int
    profile_key: str
    eligible: bool
    reason: str
    category_id: str = ""


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
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _deal_category(
    database_path: str,
    deal_id: int,
) -> str | None:
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

        value = item.get("CATEGORY_ID")

        if value in (None, ""):
            return None

        return str(value).strip()

    finally:
        connection.close()


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
            reason="policy_scope_entity_id_invalid",
        )

    if entity_type == "lead":
        # Deliberately fail closed.
        #
        # A lead has no deal CATEGORY_ID yet. Until a verified
        # lead -> business-profile binding exists, automatically
        # applying the Tourism B2C first-response SLA could
        # incorrectly score Concierge or another business line.
        return PolicyScopeDecision(
            entity_type="lead",
            entity_id=entity_id,
            profile_key="unresolved",
            eligible=False,
            reason="lead_policy_profile_unresolved",
        )

    if entity_type != "deal":
        return PolicyScopeDecision(
            entity_type=entity_type,
            entity_id=entity_id,
            profile_key="unresolved",
            eligible=False,
            reason="entity_type_has_no_policy_profile",
        )

    category_id = _deal_category(
        database_path,
        entity_id,
    )

    if category_id is None:
        return PolicyScopeDecision(
            entity_type="deal",
            entity_id=entity_id,
            profile_key="unresolved",
            eligible=False,
            reason="deal_category_missing",
        )

    contract = load_policy_contract()

    funnel = contract.binding.get("business_policy_funnel")

    if not isinstance(funnel, dict):
        return PolicyScopeDecision(
            entity_type="deal",
            entity_id=entity_id,
            profile_key="unresolved",
            eligible=False,
            reason="tourism_policy_funnel_missing",
            category_id=category_id,
        )

    tourism_category = str(funnel.get("category_id") or "").strip()

    if tourism_category and category_id == tourism_category:
        return PolicyScopeDecision(
            entity_type="deal",
            entity_id=entity_id,
            profile_key=TOURISM_B2C_PROFILE,
            eligible=True,
            reason="tourism_b2c_category_match",
            category_id=category_id,
        )

    return PolicyScopeDecision(
        entity_type="deal",
        entity_id=entity_id,
        profile_key="unbound",
        eligible=False,
        reason=("deal_policy_profile_unbound:" + category_id),
        category_id=category_id,
    )
