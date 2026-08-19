from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.services.rop_realtime_sla_orchestrator import (
    process_next_realtime_sla,
    process_realtime_sla_batch,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)
from app.storage.crm_store import CrmStore
from app.storage.rop_sla_runtime_store import (
    RopSlaRuntimeStore,
)
from app.storage.rop_source_coverage_store import (
    RopSourceCoverageStore,
)


def dt(
    hour: int,
    minute: int,
) -> datetime:
    # 06:00 UTC = 09:00 Europe/Moscow.
    return datetime(
        2026,
        8,
        19,
        hour,
        minute,
        tzinfo=UTC,
    )


async def prepare_deal(
    database_path: str,
    *,
    category_id: str = "7",
    stage_id: str = "C7:NEW",
) -> None:
    store = CrmStore(database_path)

    await store.initialize()

    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "200",
                "CATEGORY_ID": category_id,
                "STAGE_ID": stage_id,
                "DATE_CREATE": dt(
                    5,
                    0,
                ).isoformat(),
                "DATE_MODIFY": dt(
                    6,
                    0,
                ).isoformat(),
                "MOVED_TIME": dt(
                    6,
                    0,
                ).isoformat(),
            },
        ],
        modified_field="DATE_MODIFY",
    )

    await store.upsert_entities(
        "deal_stage_history",
        [
            {
                "ID": "700",
                "OWNER_ID": "200",
                "STAGE_ID": stage_id,
                "CREATED_TIME": dt(
                    6,
                    0,
                ).isoformat(),
            },
        ],
        modified_field="CREATED_TIME",
    )

    inbox = BitrixEventInboxStore(database_path)

    await inbox.initialize()

    con = sqlite3.connect(database_path)

    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS openlines_crm_links (
            chat_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS openlines_messages (
            message_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            sender_role TEXT NOT NULL,
            sender_directory_user_id TEXT,
            sent_at TEXT
        );
        """
    )

    con.commit()
    con.close()


async def add_coverage(
    database_path: str,
) -> None:
    store = RopSourceCoverageStore(database_path)

    for source in (
        "crm_realtime",
        "openlines",
        "voximplant_realtime",
    ):
        await store.add_interval(
            source_key=source,
            coverage_start=dt(
                5,
                0,
            ),
            coverage_end=None,
            evidence_kind=("test_verified"),
        )


async def complete_event(
    database_path: str,
    *,
    event_key: str,
    event_name: str,
    entity_type: str,
    entity_id: str,
    data: dict | None = None,
    call_id: str = "",
):
    store = BitrixEventInboxStore(database_path)

    result = await store.enqueue(
        event_key=event_key,
        event_name=event_name,
        event_ts=int(
            dt(
                6,
                5,
            ).timestamp()
        ),
        event_handler_id="1",
        entity_type=entity_type,
        entity_id=entity_id,
        call_id=call_id,
        actor_user_id="55",
        member_id="member",
        domain="example.test",
        data_json=json.dumps(
            data or {},
        ),
    )

    claimed = await store.claim_next()

    assert claimed is not None
    assert claimed.inbox_id == result.inbox_id

    await store.complete(
        claimed.inbox_id,
        result_code="test_completed",
    )

    return claimed


async def test_411a6a_b2c_deal_event_writes_open_evaluation(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    event = await complete_event(
        database_path,
        event_key="deal-1",
        event_name="ONCRMDEALUPDATE",
        entity_type="deal",
        entity_id="200",
    )

    result = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result is not None
    assert result.outcome == "completed"
    assert result.targets_observed == 1
    assert result.evaluations_written == 1

    runtime = RopSlaRuntimeStore(database_path)

    rows = await runtime.evaluations_for_inbox(event.inbox_id)

    assert len(rows) == 1
    assert rows[0].policy_profile == "tourism_b2c"
    assert rows[0].rule_key == "stale_deal"
    assert rows[0].state == "ready"
    assert rows[0].verdict == "open"


async def test_411a6a_concierge_deal_is_scope_skipped(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(
        database_path,
        category_id="2",
        stage_id="C2:NEW",
    )

    event = await complete_event(
        database_path,
        event_key="concierge-1",
        event_name="ONCRMDEALUPDATE",
        entity_type="deal",
        entity_id="200",
    )

    result = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result is not None
    assert result.evaluations_written == 0
    assert "deal_policy_profile_unbound:2" in result.notes

    runtime = RopSlaRuntimeStore(database_path)

    assert await runtime.evaluations_for_inbox(event.inbox_id) == ()


async def test_411a6a_lead_is_not_given_b2c_sla_without_profile(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    inbox = BitrixEventInboxStore(database_path)

    await inbox.initialize()

    event = await complete_event(
        database_path,
        event_key="lead-1",
        event_name="ONCRMLEADUPDATE",
        entity_type="lead",
        entity_id="100",
    )

    result = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result is not None
    assert result.evaluations_written == 0
    assert "lead_policy_profile_unresolved" in result.notes

    runtime = RopSlaRuntimeStore(database_path)

    assert await runtime.evaluations_for_inbox(event.inbox_id) == ()


async def test_411a6a_activity_event_resolves_deal_owner(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    crm = CrmStore(database_path)

    await crm.upsert_entities(
        "activity",
        [
            {
                "ID": "900",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "200",
                "TYPE_ID": "3",
                "COMPLETED": "Y",
                "LAST_UPDATED": dt(
                    6,
                    5,
                ).isoformat(),
            },
        ],
        modified_field="LAST_UPDATED",
    )

    await complete_event(
        database_path,
        event_key="activity-1",
        event_name="ONCRMACTIVITYUPDATE",
        entity_type="activity",
        entity_id="900",
    )

    result = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result is not None
    assert result.targets_observed == 1
    assert result.evaluations_written == 1


async def test_411a6a_call_via_activity_resolves_and_resets_stage_timer(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    crm = CrmStore(database_path)

    await crm.upsert_entities(
        "activity",
        [
            {
                "ID": "900",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "200",
                "TYPE_ID": "2",
                "COMPLETED": "Y",
                "RESPONSIBLE_ID": "55",
            },
        ],
        modified_field=None,
    )

    inbox = BitrixEventInboxStore(database_path)

    for (
        key,
        name,
        minute,
        failed_code,
    ) in (
        (
            "call-start",
            "ONVOXIMPLANTCALLSTART",
            10,
            "",
        ),
        (
            "call-end",
            "ONVOXIMPLANTCALLEND",
            11,
            "200",
        ),
    ):
        enqueue = await inbox.enqueue(
            event_key=key,
            event_name=name,
            event_ts=int(
                dt(
                    6,
                    minute,
                ).timestamp()
            ),
            event_handler_id="1",
            entity_type="call",
            entity_id="",
            call_id="call-1",
            actor_user_id="55",
            member_id="member",
            domain="example.test",
            data_json=json.dumps(
                {
                    "CALL_ID": "call-1",
                    "USER_ID": "55",
                    "CALL_TYPE": "1",
                    "CRM_ACTIVITY_ID": "900",
                    **(
                        {
                            "CALL_FAILED_CODE": failed_code,
                        }
                        if failed_code
                        else {}
                    ),
                }
            ),
        )

        claimed = await inbox.claim_next()

        assert claimed is not None
        assert claimed.inbox_id == enqueue.inbox_id

        await inbox.record_call_evidence(
            claimed,
            call_failed_code=failed_code,
            crm_activity_id="900",
        )

        await inbox.complete(
            claimed.inbox_id,
            result_code="test_call",
        )

    results = await process_realtime_sla_batch(
        database_path,
        limit=10,
        as_of=dt(
            6,
            20,
        ),
    )

    assert len(results) == 2
    assert all(item.evaluations_written == 1 for item in results)

    runtime = RopSlaRuntimeStore(database_path)

    rows = await runtime.evaluations_for_inbox(results[-1].inbox_id)

    assert len(rows) == 1

    payload = json.loads(rows[0].evaluation_json)

    assert payload["verdict"] == "open"

    assert payload["details"]["last_activity_kind"] == "outbound_call"

    assert payload["elapsed_business_seconds"] == 600


async def test_411a6a_dispatch_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    await complete_event(
        database_path,
        event_key="deal-once",
        event_name="ONCRMDEALUPDATE",
        entity_type="deal",
        entity_id="200",
    )

    first = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    second = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert first is not None
    assert second is None


async def test_411a6a_failed_dispatch_can_retry(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    inbox = BitrixEventInboxStore(database_path)

    await inbox.initialize()

    event = await complete_event(
        database_path,
        event_key="retry-1",
        event_name="ONCRMDEALUPDATE",
        entity_type="deal",
        entity_id="200",
    )

    runtime = RopSlaRuntimeStore(database_path)

    first = await runtime.claim_next(max_attempts=3)

    assert first is not None
    assert first.attempts == 1

    await runtime.fail(
        event.inbox_id,
        error_code="TEST_ERROR",
    )

    second = await runtime.claim_next(max_attempts=3)

    assert second is not None
    assert second.inbox_id == event.inbox_id
    assert second.attempts == 2


async def test_411a6a_evaluation_log_does_not_copy_trigger_payload(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    event = await complete_event(
        database_path,
        event_key="safe-log",
        event_name="ONCRMDEALUPDATE",
        entity_type="deal",
        entity_id="200",
        data={
            "PRIVATE_NOTE": "never-copy-this-trigger-payload",
        },
    )

    result = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result is not None
    assert result.evaluations_written == 1

    runtime = RopSlaRuntimeStore(database_path)

    rows = await runtime.evaluations_for_inbox(event.inbox_id)

    assert len(rows) == 1

    assert "never-copy-this-trigger-payload" not in rows[0].evaluation_json
