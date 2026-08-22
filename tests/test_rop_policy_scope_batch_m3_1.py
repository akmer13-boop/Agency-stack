from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.services import rop_policy_scope as scope_module
from app.services.rop_policy_scope import (
    PolicyScopeDecision,
    resolve_deal_policy_scopes,
    resolve_lead_policy_scopes,
    resolve_policy_scope,
)
from app.storage.rop_lead_policy_profile_store import (
    RopLeadPolicyProfileStore,
)


def prepare(database_path: str) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE crm_active_entities (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id)
        )
        """
    )
    connection.commit()
    connection.close()


def put(
    database_path: str,
    entity_type: str,
    entity_id: int,
    payload: dict[str, object],
) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        INSERT INTO crm_active_entities (
            entity_type,
            entity_id,
            payload_json
        ) VALUES (?, ?, ?)
        """,
        (
            entity_type,
            str(entity_id),
            json.dumps(payload),
        ),
    )
    connection.commit()
    connection.close()


def signature(
    decision: PolicyScopeDecision,
) -> tuple[object, ...]:
    return (
        decision.entity_type,
        decision.entity_id,
        decision.profile_key,
        decision.eligible,
        decision.reason,
        decision.category_id,
        decision.department_ids,
        decision.evidence_kind,
    )


def test_m3_1_batch_scope_matches_truthful_single_lead_resolution(
    tmp_path: Path,
) -> None:
    template = str(tmp_path / "template.db")
    sequential = str(tmp_path / "sequential.db")
    batched = str(tmp_path / "batched.db")
    prepare(template)

    for user_id, department_id in (
        (10, 19),
        (11, 20),
        (12, 103),
    ):
        put(
            template,
            "user",
            user_id,
            {"UF_DEPARTMENT": [str(department_id)]},
        )

    for lead_id, user_id in (
        (100, 10),
        (101, 11),
        (102, 12),
        (103, 12),
        (104, 12),
        (105, 12),
        (106, 12),
    ):
        put(
            template,
            "lead",
            lead_id,
            {"ASSIGNED_BY_ID": str(user_id)},
        )

    for deal_id, lead_id, category_id in (
        (200, 102, 7),
        (201, 103, 2),
        (202, 104, 7),
        (203, 104, 2),
    ):
        put(
            template,
            "deal",
            deal_id,
            {
                "LEAD_ID": str(lead_id),
                "CATEGORY_ID": str(category_id),
            },
        )

    RopLeadPolicyProfileStore(
        template
    ).confirm_tourism_b2c(
        lead_id=106,
        evidence_kind="historical_b2c",
        evidence_ref="test:106",
    )
    shutil.copyfile(template, sequential)
    shutil.copyfile(template, batched)

    lead_ids = (
        100,
        101,
        102,
        103,
        104,
        105,
        106,
        999,
    )
    expected = {
        lead_id: resolve_policy_scope(
            sequential,
            entity_type="lead",
            entity_id=lead_id,
        )
        for lead_id in lead_ids
    }
    actual = resolve_lead_policy_scopes(
        batched,
        lead_ids,
    )

    assert {
        lead_id: signature(decision)
        for lead_id, decision in actual.items()
    } == {
        lead_id: signature(decision)
        for lead_id, decision in expected.items()
    }

    stored = RopLeadPolicyProfileStore(
        batched
    ).get_many(lead_ids)
    assert set(stored) == {100, 102, 106}


def test_m3_1_batch_scope_uses_constant_connection_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = str(tmp_path / "many.db")
    prepare(database_path)
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        INSERT INTO crm_active_entities (
            entity_type,
            entity_id,
            payload_json
        ) VALUES ('user', '10', ?)
        """,
        (json.dumps({"UF_DEPARTMENT": ["19"]}),),
    )
    connection.executemany(
        """
        INSERT INTO crm_active_entities (
            entity_type,
            entity_id,
            payload_json
        ) VALUES ('lead', ?, ?)
        """,
        (
            (
                str(lead_id),
                json.dumps({"ASSIGNED_BY_ID": "10"}),
            )
            for lead_id in range(1, 601)
        ),
    )
    connection.commit()
    connection.close()

    real_connect = sqlite3.connect
    connection_count = 0

    def counted_connect(*args, **kwargs):
        nonlocal connection_count
        connection_count += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(
        scope_module.sqlite3,
        "connect",
        counted_connect,
    )

    decisions = resolve_lead_policy_scopes(
        database_path,
        range(1, 601),
    )

    assert len(decisions) == 600
    assert all(
        decision.eligible
        for decision in decisions.values()
    )
    assert connection_count <= 5


def test_m3_2_batch_deal_scope_matches_single_deal_resolution(
    tmp_path: Path,
) -> None:
    template = str(tmp_path / "template.db")
    sequential = str(tmp_path / "sequential.db")
    batched = str(tmp_path / "batched.db")
    prepare(template)

    for user_id, department_id in (
        (10, 19),
        (44, 44),
    ):
        put(
            template,
            "user",
            user_id,
            {"UF_DEPARTMENT": [str(department_id)]},
        )

    for deal_id, payload in (
        (
            200,
            {
                "CATEGORY_ID": "7",
                "ASSIGNED_BY_ID": "10",
            },
        ),
        (
            201,
            {
                "CATEGORY_ID": "7",
                "ASSIGNED_BY_ID": "44",
            },
        ),
        (
            202,
            {
                "CATEGORY_ID": "2",
                "ASSIGNED_BY_ID": "10",
            },
        ),
        (
            203,
            {"ASSIGNED_BY_ID": "10"},
        ),
    ):
        put(
            template,
            "deal",
            deal_id,
            payload,
        )

    shutil.copyfile(template, sequential)
    shutil.copyfile(template, batched)
    deal_ids = (200, 201, 202, 203, 999)
    expected = {
        deal_id: resolve_policy_scope(
            sequential,
            entity_type="deal",
            entity_id=deal_id,
        )
        for deal_id in deal_ids
    }
    actual = resolve_deal_policy_scopes(
        batched,
        deal_ids,
    )

    assert {
        deal_id: signature(decision)
        for deal_id, decision in actual.items()
    } == {
        deal_id: signature(decision)
        for deal_id, decision in expected.items()
    }
