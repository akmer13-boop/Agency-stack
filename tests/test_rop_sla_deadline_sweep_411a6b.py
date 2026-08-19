from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.services.rop_realtime_sla_orchestrator import (
    process_next_realtime_sla,
)
from app.services.rop_sla_deadline_sweep import (
    process_next_sla_deadline,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)
from app.storage.crm_store import (
    CrmStore,
)
from app.storage.rop_sla_deadline_sweep_store import (
    RopSlaDeadlineSweepStore,
)
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
) -> None:
    crm = CrmStore(database_path)

    await crm.initialize()

    await crm.upsert_entities(
        "deal",
        [
            {
                "ID": "200",
                "CATEGORY_ID": category_id,
                "STAGE_ID": "C7:NEW",
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

    await crm.upsert_entities(
        "deal_stage_history",
        [
            {
                "ID": "700",
                "OWNER_ID": "200",
                "STAGE_ID": "C7:NEW",
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
    *,
    end_at: datetime | None = None,
) -> None:
    coverage = RopSourceCoverageStore(database_path)

    for source in (
        "crm_realtime",
        "openlines",
        "voximplant_realtime",
    ):
        await coverage.add_interval(
            source_key=source,
            coverage_start=dt(
                5,
                0,
            ),
            coverage_end=end_at,
            evidence_kind="test_verified",
        )


async def add_completed_deal_event(
    database_path: str,
    *,
    event_key: str,
    event_minute: int,
) -> int:
    inbox = BitrixEventInboxStore(database_path)

    result = await inbox.enqueue(
        event_key=event_key,
        event_name="ONCRMDEALUPDATE",
        event_ts=int(
            dt(
                6,
                event_minute,
            ).timestamp()
        ),
        event_handler_id="1",
        entity_type="deal",
        entity_id="200",
        call_id="",
        actor_user_id="55",
        member_id="member",
        domain="example.test",
        data_json=json.dumps(
            {
                "FIELDS": {
                    "ID": "200",
                },
            }
        ),
    )

    claimed = await inbox.claim_next()

    assert claimed is not None

    await inbox.complete(
        claimed.inbox_id,
        result_code="test_completed",
    )

    return result.inbox_id


async def create_open_evaluation(
    database_path: str,
    *,
    as_of: datetime,
    event_key: str = "deal-open",
) -> int:
    inbox_id = await add_completed_deal_event(
        database_path,
        event_key=event_key,
        event_minute=5,
    )

    result = await process_next_realtime_sla(
        database_path,
        as_of=as_of,
    )

    assert result is not None
    assert result.outcome == "completed"
    assert result.evaluations_written == 1

    runtime = RopSlaRuntimeStore(database_path)

    rows = await runtime.evaluations_for_inbox(inbox_id)

    assert len(rows) == 1
    assert rows[0].verdict == "open"

    return rows[0].evaluation_id


def add_manager_message(
    database_path: str,
    *,
    minute: int,
) -> None:
    con = sqlite3.connect(database_path)

    con.execute(
        """
        INSERT INTO openlines_crm_links
        VALUES ('77', 'deal', '200')
        """
    )

    con.execute(
        """
        INSERT INTO openlines_messages
        VALUES (?, '77', 'manager', '55', ?)
        """,
        (
            "501",
            dt(
                6,
                minute,
            ).isoformat(),
        ),
    )

    con.commit()
    con.close()


async def test_411a6b_before_deadline_does_nothing(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    await create_open_evaluation(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    result = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            14,
        ),
    )

    assert result is None


async def test_411a6b_due_open_becomes_attention_without_new_event(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    evaluation_id = await create_open_evaluation(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    result = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            15,
        ),
    )

    assert result is not None
    assert result.outcome == "completed"
    assert result.state == "ready"
    assert result.verdict == "attention"

    store = RopSlaDeadlineSweepStore(database_path)

    stored = await store.get(evaluation_id)

    assert stored is not None
    assert stored.status == "completed"
    assert stored.verdict == "attention"

    payload = json.loads(stored.evaluation_json)

    assert payload["elapsed_business_seconds"] == 900


async def test_411a6b_completed_sweep_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    await create_open_evaluation(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    first = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            15,
        ),
    )

    second = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            20,
        ),
    )

    assert first is not None
    assert second is None


async def test_411a6b_newer_event_evaluation_supersedes_old_deadline(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    old_evaluation_id = await create_open_evaluation(
        database_path,
        as_of=dt(
            6,
            10,
        ),
        event_key="event-1",
    )

    add_manager_message(
        database_path,
        minute=12,
    )

    await add_completed_deal_event(
        database_path,
        event_key="event-2",
        event_minute=12,
    )

    second = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            12,
        ),
    )

    assert second is not None
    assert second.evaluations_written == 1

    sweep = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            15,
        ),
    )

    assert sweep is None

    store = RopSlaDeadlineSweepStore(database_path)

    assert await store.get(old_evaluation_id) is None


async def test_411a6b_coverage_gap_at_deadline_fails_closed(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(
        database_path,
        end_at=dt(
            6,
            14,
        ),
    )

    evaluation_id = await create_open_evaluation(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    result = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            15,
        ),
    )

    assert result is not None
    assert result.outcome == "completed"
    assert result.state == "blocked"
    assert result.verdict == "blocked"

    store = RopSlaDeadlineSweepStore(database_path)

    stored = await store.get(evaluation_id)

    assert stored is not None

    payload = json.loads(stored.evaluation_json)

    assert "source_coverage_gap:crm_realtime" in payload["reasons"]


async def test_411a6b_concierge_has_no_tourism_deadline_candidate(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(
        database_path,
        category_id="2",
    )

    await add_completed_deal_event(
        database_path,
        event_key="concierge",
        event_minute=5,
    )

    dispatch = await process_next_realtime_sla(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert dispatch is not None
    assert dispatch.evaluations_written == 0

    sweep = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            30,
        ),
    )

    assert sweep is None


async def test_411a6b_failed_claim_can_retry(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    evaluation_id = await create_open_evaluation(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    store = RopSlaDeadlineSweepStore(database_path)

    first = await store.claim_due(
        as_of=dt(
            6,
            15,
        ),
        max_attempts=3,
    )

    assert first is not None
    assert first.attempts == 1

    await store.fail(
        first,
        error_code="TEST_ERROR",
    )

    second = await store.claim_due(
        as_of=dt(
            6,
            16,
        ),
        max_attempts=3,
    )

    assert second is not None
    assert second.source_evaluation_id == evaluation_id
    assert second.attempts == 2


async def test_411a6b_sweep_json_contains_no_trigger_payload(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare_deal(database_path)

    await add_coverage(database_path)

    evaluation_id = await create_open_evaluation(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    result = await process_next_sla_deadline(
        database_path,
        as_of=dt(
            6,
            15,
        ),
    )

    assert result is not None

    store = RopSlaDeadlineSweepStore(database_path)

    stored = await store.get(evaluation_id)

    assert stored is not None

    assert "PRIVATE_NOTE" not in stored.evaluation_json
