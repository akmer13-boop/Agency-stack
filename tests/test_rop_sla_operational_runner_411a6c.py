from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.rop_sla_operational_runner import (
    run_operational_sla_cycle,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)
from app.storage.crm_store import (
    CrmStore,
)
from app.storage.rop_operational_coverage_store import (
    REQUIRED_OPERATIONAL_SOURCES,
    RopOperationalCoverageStore,
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


async def prepare(
    database_path: str,
) -> None:
    crm = CrmStore(database_path)

    await crm.initialize()

    await crm.upsert_entities(
        "deal",
        [
            {
                "ID": "200",
                "CATEGORY_ID": "7",
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


async def add_event(
    database_path: str,
) -> int:
    inbox = BitrixEventInboxStore(database_path)

    queued = await inbox.enqueue(
        event_key="deal-event",
        event_name="ONCRMDEALUPDATE",
        event_ts=int(
            dt(
                6,
                5,
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

    return queued.inbox_id


async def initialize_coverage(
    database_path: str,
    *,
    through: datetime,
) -> None:
    store = RopOperationalCoverageStore(database_path)

    for source in REQUIRED_OPERATIONAL_SOURCES:
        await store.initialize_source(
            source_key=source,
            coverage_start=dt(
                5,
                0,
            ),
            covered_through=through,
            observed_at=through,
            evidence_kind=("test_verified_watermark"),
        )


async def advance_all(
    database_path: str,
    *,
    through: datetime,
) -> None:
    store = RopOperationalCoverageStore(database_path)

    for source in REQUIRED_OPERATIONAL_SOURCES:
        await store.advance_source(
            source_key=source,
            covered_through=through,
            observed_at=through,
            evidence_kind=("test_verified_watermark"),
        )


async def test_411a6c_missing_watermarks_guard_without_consuming_event(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare(database_path)

    inbox_id = await add_event(database_path)

    result = await run_operational_sla_cycle(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result.ready is False

    assert set(result.missing_sources) == set(REQUIRED_OPERATIONAL_SOURCES)

    runtime = RopSlaRuntimeStore(database_path)

    assert await runtime.dispatch_for_inbox(inbox_id) is None


async def test_411a6c_complete_watermarks_allow_event_cycle(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare(database_path)

    inbox_id = await add_event(database_path)

    await initialize_coverage(
        database_path,
        through=dt(
            6,
            10,
        ),
    )

    result = await run_operational_sla_cycle(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result.ready is True
    assert result.events_processed == 1
    assert result.evaluations_written == 1

    runtime = RopSlaRuntimeStore(database_path)

    rows = await runtime.evaluations_for_inbox(inbox_id)

    assert len(rows) == 1
    assert rows[0].verdict == "open"


async def test_411a6c_one_lagging_source_guards_cycle(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare(database_path)

    inbox_id = await add_event(database_path)

    store = RopOperationalCoverageStore(database_path)

    for source in REQUIRED_OPERATIONAL_SOURCES:
        through = (
            dt(
                6,
                9,
            )
            if source == "openlines"
            else dt(
                6,
                10,
            )
        )

        await store.initialize_source(
            source_key=source,
            coverage_start=dt(
                5,
                0,
            ),
            covered_through=through,
            observed_at=dt(
                6,
                10,
            ),
            evidence_kind="verified",
        )

    result = await run_operational_sla_cycle(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result.ready is False

    assert result.lagging_sources == ("openlines",)

    runtime = RopSlaRuntimeStore(database_path)

    assert await runtime.dispatch_for_inbox(inbox_id) is None


async def test_411a6c_advance_watermark_enables_silent_deadline_transition(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    await prepare(database_path)

    inbox_id = await add_event(database_path)

    await initialize_coverage(
        database_path,
        through=dt(
            6,
            10,
        ),
    )

    first = await run_operational_sla_cycle(
        database_path,
        as_of=dt(
            6,
            10,
        ),
    )

    assert first.ready is True
    assert first.events_processed == 1

    runtime = RopSlaRuntimeStore(database_path)

    rows = await runtime.evaluations_for_inbox(inbox_id)

    assert len(rows) == 1
    evaluation_id = rows[0].evaluation_id

    await advance_all(
        database_path,
        through=dt(
            6,
            15,
        ),
    )

    second = await run_operational_sla_cycle(
        database_path,
        as_of=dt(
            6,
            15,
        ),
    )

    assert second.ready is True
    assert second.events_processed == 0
    assert second.deadlines_processed == 1

    sweep = RopSlaDeadlineSweepStore(database_path)

    stored = await sweep.get(evaluation_id)

    assert stored is not None
    assert stored.verdict == "attention"


async def test_411a6c_watermark_cannot_move_backwards(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    store = RopOperationalCoverageStore(database_path)

    await store.initialize_source(
        source_key="crm_realtime",
        coverage_start=dt(
            5,
            0,
        ),
        covered_through=dt(
            6,
            10,
        ),
        observed_at=dt(
            6,
            10,
        ),
        evidence_kind="verified",
    )

    with pytest.raises(
        ValueError,
        match="coverage_watermark_regression",
    ):
        await store.advance_source(
            source_key="crm_realtime",
            covered_through=dt(
                6,
                9,
            ),
            observed_at=dt(
                6,
                10,
            ),
            evidence_kind="verified",
        )


async def test_411a6c_watermark_cannot_claim_future_coverage(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    store = RopOperationalCoverageStore(database_path)

    with pytest.raises(
        ValueError,
        match="covered_through_after_observed_at",
    ):
        await store.initialize_source(
            source_key="crm_realtime",
            coverage_start=dt(
                5,
                0,
            ),
            covered_through=dt(
                6,
                11,
            ),
            observed_at=dt(
                6,
                10,
            ),
            evidence_kind="verified",
        )


async def test_411a6c_advance_requires_initialization(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    store = RopOperationalCoverageStore(database_path)

    with pytest.raises(
        ValueError,
        match="coverage_source_not_initialized",
    ):
        await store.advance_source(
            source_key="crm_realtime",
            covered_through=dt(
                6,
                10,
            ),
            observed_at=dt(
                6,
                10,
            ),
            evidence_kind="verified",
        )


async def test_411a6c_safe_watermarks_create_only_bounded_intervals(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "test.db")

    operational = RopOperationalCoverageStore(database_path)

    await operational.initialize_source(
        source_key="crm_realtime",
        coverage_start=dt(
            5,
            0,
        ),
        covered_through=dt(
            6,
            10,
        ),
        observed_at=dt(
            6,
            10,
        ),
        evidence_kind="verified",
    )

    await operational.advance_source(
        source_key="crm_realtime",
        covered_through=dt(
            6,
            15,
        ),
        observed_at=dt(
            6,
            15,
        ),
        evidence_kind="verified",
    )

    coverage = RopSourceCoverageStore(database_path)

    intervals = await coverage.list_intervals("crm_realtime")

    assert len(intervals) == 2

    assert all(item.coverage_end_ts is not None for item in intervals)
