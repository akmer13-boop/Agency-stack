from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.services.rop_policy_scope import (
    TOURISM_B2C_PROFILE,
    resolve_policy_scope,
)
from app.services.rop_realtime_sla_orchestrator import (
    resolve_sla_targets,
)
from app.storage.rop_lead_policy_profile_store import (
    RopLeadPolicyProfileStore,
)
from app.storage.rop_sla_runtime_store import (
    SlaDispatchEvent,
)


def prepare(
    database_path: str,
) -> None:
    connection = sqlite3.connect(database_path)

    connection.execute(
        """
        CREATE TABLE crm_active_entities (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (
                entity_type,
                entity_id
            )
        )
        """
    )

    connection.commit()
    connection.close()


def put(
    database_path: str,
    entity_type: str,
    entity_id: int,
    payload: dict,
) -> None:
    value = dict(payload)

    value.setdefault(
        "ID",
        str(entity_id),
    )

    connection = sqlite3.connect(database_path)

    connection.execute(
        """
        INSERT OR REPLACE INTO
            crm_active_entities (
                entity_type,
                entity_id,
                payload_json
            )
        VALUES (?, ?, ?)
        """,
        (
            entity_type,
            str(entity_id),
            json.dumps(value),
        ),
    )

    connection.commit()
    connection.close()


def delete_entity(
    database_path: str,
    entity_type: str,
    entity_id: int,
) -> None:
    connection = sqlite3.connect(database_path)

    connection.execute(
        """
        DELETE FROM crm_active_entities
        WHERE entity_type = ?
          AND entity_id = ?
        """,
        (
            entity_type,
            str(entity_id),
        ),
    )

    connection.commit()
    connection.close()


def seed_user(
    database_path: str,
    user_id: int,
    department_id: int,
) -> None:
    put(
        database_path,
        "user",
        user_id,
        {
            "UF_DEPARTMENT": [str(department_id)],
            "ACTIVE": True,
        },
    )


def seed_lead(
    database_path: str,
    lead_id: int,
    user_id: int,
) -> None:
    put(
        database_path,
        "lead",
        lead_id,
        {
            "ASSIGNED_BY_ID": str(user_id),
        },
    )


def seed_deal(
    database_path: str,
    deal_id: int,
    *,
    category_id: int,
    lead_id: int | None = None,
    user_id: int | None = None,
) -> None:
    payload = {
        "CATEGORY_ID": str(category_id),
    }

    if lead_id is not None:
        payload["LEAD_ID"] = str(lead_id)

    if user_id is not None:
        payload["ASSIGNED_BY_ID"] = str(user_id)

    put(
        database_path,
        "deal",
        deal_id,
        payload,
    )


def test_412a_department_19_resolves_b2c_and_persists(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_user(
        database_path,
        10,
        19,
    )

    seed_lead(
        database_path,
        100,
        10,
    )

    result = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert result.eligible is True
    assert result.profile_key == TOURISM_B2C_PROFILE
    assert result.reason == "lead_b2c_department_match"

    stored = RopLeadPolicyProfileStore(database_path).get(100)

    assert stored is not None
    assert stored.profile_key == TOURISM_B2C_PROFILE


def test_412a_b2c_profile_is_sticky_after_service_transfer(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_user(
        database_path,
        10,
        19,
    )

    seed_user(
        database_path,
        11,
        105,
    )

    seed_lead(
        database_path,
        100,
        10,
    )

    first = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert first.eligible is True

    seed_lead(
        database_path,
        100,
        11,
    )

    second = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert second.eligible is True

    assert second.reason == "lead_b2c_profile_sticky"


def test_412a_explicit_non_b2c_department_overrides_sticky(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_user(
        database_path,
        10,
        19,
    )

    seed_user(
        database_path,
        44,
        44,
    )

    seed_lead(
        database_path,
        100,
        10,
    )

    assert (
        resolve_policy_scope(
            database_path,
            entity_type="lead",
            entity_id=100,
        ).eligible
        is True
    )

    seed_lead(
        database_path,
        100,
        44,
    )

    result = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert result.eligible is False

    assert result.reason == "lead_department_excluded:concierge"


def test_412a_explicit_departments_are_excluded(
    tmp_path: Path,
) -> None:
    for (
        department_id,
        reason,
    ) in (
        (
            20,
            "lead_department_excluded:b2b",
        ),
        (
            24,
            "lead_department_excluded:russian_tour",
        ),
        (
            36,
            "lead_department_excluded:concierge",
        ),
        (
            41,
            "lead_department_excluded:concierge",
        ),
        (
            44,
            "lead_department_excluded:concierge",
        ),
    ):
        database_path = str(tmp_path / ("test_" + str(department_id) + ".db"))

        prepare(database_path)

        seed_user(
            database_path,
            10,
            department_id,
        )

        seed_lead(
            database_path,
            100,
            10,
        )

        result = resolve_policy_scope(
            database_path,
            entity_type="lead",
            entity_id=100,
        )

        assert result.eligible is False

        assert result.reason == reason


def test_412a_neutral_departments_remain_unresolved(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    for (
        user_id,
        department_id,
    ) in (
        (
            10,
            103,
        ),
        (
            11,
            1,
        ),
        (
            12,
            21,
        ),
        (
            13,
            105,
        ),
    ):
        seed_user(
            database_path,
            user_id,
            department_id,
        )

    for (
        lead_id,
        user_id,
    ) in (
        (
            100,
            10,
        ),
        (
            101,
            11,
        ),
        (
            102,
            12,
        ),
        (
            103,
            13,
        ),
    ):
        seed_lead(
            database_path,
            lead_id,
            user_id,
        )

        result = resolve_policy_scope(
            database_path,
            entity_type="lead",
            entity_id=lead_id,
        )

        assert result.eligible is False

        assert result.reason == "lead_policy_profile_unresolved"


def test_412a_linked_category7_deal_resolves_and_sticks(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_user(
        database_path,
        10,
        103,
    )

    seed_lead(
        database_path,
        100,
        10,
    )

    seed_deal(
        database_path,
        200,
        category_id=7,
        lead_id=100,
    )

    first = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert first.eligible is True

    assert first.reason == "lead_linked_b2c_deal"

    delete_entity(
        database_path,
        "deal",
        200,
    )

    second = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert second.eligible is True

    assert second.reason == "lead_b2c_profile_sticky"


def test_412a_non_b2c_deal_does_not_resolve_b2c(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_user(
        database_path,
        10,
        103,
    )

    seed_lead(
        database_path,
        100,
        10,
    )

    seed_deal(
        database_path,
        200,
        category_id=2,
        lead_id=100,
    )

    result = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert result.eligible is False

    assert result.reason == "lead_linked_non_b2c_deal"


def test_412a_multiple_linked_funnels_fail_closed(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_user(
        database_path,
        10,
        103,
    )

    seed_lead(
        database_path,
        100,
        10,
    )

    seed_deal(
        database_path,
        200,
        category_id=7,
        lead_id=100,
    )

    seed_deal(
        database_path,
        201,
        category_id=2,
        lead_id=100,
    )

    result = resolve_policy_scope(
        database_path,
        entity_type="lead",
        entity_id=100,
    )

    assert result.eligible is False

    assert result.reason == "lead_linked_multiple_funnels"


def test_412a_category7_deal_owned_by_concierge_is_excluded(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_user(
        database_path,
        44,
        44,
    )

    seed_deal(
        database_path,
        200,
        category_id=7,
        user_id=44,
    )

    result = resolve_policy_scope(
        database_path,
        entity_type="deal",
        entity_id=200,
    )

    assert result.eligible is False

    assert result.reason == "deal_department_excluded:concierge"


def test_412a_deal_event_also_targets_linked_lead(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    prepare(database_path)

    seed_deal(
        database_path,
        200,
        category_id=7,
        lead_id=100,
    )

    event = SlaDispatchEvent(
        inbox_id=1,
        event_key="deal-event",
        event_name="ONCRMDEALUPDATE",
        event_ts=1,
        entity_type="deal",
        entity_id="200",
        call_id="",
        attempts=1,
    )

    targets, notes = resolve_sla_targets(
        database_path,
        event,
    )

    assert notes == ()

    assert {
        (
            item.entity_type,
            item.entity_id,
        )
        for item in targets
    } == {
        (
            "deal",
            200,
        ),
        (
            "lead",
            100,
        ),
    }
