from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.services.bitrix_event_processor import (
    process_bitrix_event_batch,
)
from app.services.bitrix_realtime_events import (
    ingest_bitrix_event,
)
from app.services.rop_policy_evaluation import (
    EvaluationState,
    EvaluationVerdict,
)
from app.services.rop_realtime_stage_sla import (
    evaluate_realtime_deal_stage_sla,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)
from app.storage.crm_store import (
    CrmStore,
)
from app.storage.rop_source_coverage_store import (
    RopSourceCoverageStore,
)

TOKEN = "test-application-token"


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


def settings(
    tmp_path: Path,
) -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "events.db"),
        bitrix24_webhook_url=("https://example.test/rest/1/abcdefgh/"),
    )


async def prepare_deal(
    config: Settings,
    *,
    stage_id: str = "C7:NEW",
    history: bool = True,
    entered_at: datetime | None = None,
) -> None:
    entered = entered_at or dt(
        6,
        0,
    )

    store = CrmStore(config.database_path)

    await store.initialize()

    await store.upsert_entities(
        "deal",
        [
            {
                "ID": "200",
                "CATEGORY_ID": "7",
                "STAGE_ID": stage_id,
                "DATE_CREATE": dt(
                    5,
                    0,
                ).isoformat(),
                "DATE_MODIFY": entered.isoformat(),
                "MOVED_TIME": entered.isoformat(),
            },
        ],
        modified_field=("DATE_MODIFY"),
    )

    if history:
        await store.upsert_entities(
            "deal_stage_history",
            [
                {
                    "ID": "700",
                    "OWNER_ID": "200",
                    "STAGE_ID": stage_id,
                    "CREATED_TIME": entered.isoformat(),
                },
            ],
            modified_field=("CREATED_TIME"),
        )

    inbox = BitrixEventInboxStore(config.database_path)

    await inbox.initialize()

    con = sqlite3.connect(config.database_path)

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


async def complete_coverage(
    config: Settings,
    *,
    crm_start: datetime | None = None,
) -> None:
    store = RopSourceCoverageStore(config.database_path)

    for (
        source,
        start,
    ) in (
        (
            "crm_realtime",
            crm_start
            or dt(
                5,
                0,
            ),
        ),
        (
            "openlines",
            dt(
                5,
                0,
            ),
        ),
        (
            "voximplant_realtime",
            dt(
                5,
                0,
            ),
        ),
    ):
        await store.add_interval(
            source_key=source,
            coverage_start=start,
            coverage_end=None,
            evidence_kind=("test_verified"),
        )


def add_manager_message(
    database_path: str,
    *,
    minute: int,
) -> None:
    con = sqlite3.connect(database_path)

    con.execute(
        """
        INSERT INTO openlines_crm_links
        VALUES (
            '77',
            'deal',
            '200'
        )
        """
    )

    con.execute(
        """
        INSERT INTO openlines_messages
        VALUES (
            ?,
            '77',
            'manager',
            '55',
            ?
        )
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


async def add_email(
    config: Settings,
    *,
    activity_id: str,
    minute: int,
    direction: str,
) -> None:
    store = CrmStore(config.database_path)

    await store.upsert_entities(
        "activity",
        [
            {
                "ID": activity_id,
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "200",
                "TYPE_ID": "4",
                "DIRECTION": direction,
                "COMPLETED": "Y",
                "END_TIME": dt(
                    6,
                    minute,
                ).isoformat(),
                "LAST_UPDATED": dt(
                    6,
                    minute,
                ).isoformat(),
                "RESPONSIBLE_ID": "55",
            },
        ],
        modified_field=("LAST_UPDATED"),
    )


async def add_call_activity(
    config: Settings,
) -> None:
    store = CrmStore(config.database_path)

    await store.upsert_entities(
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


async def ingest_call(
    config: Settings,
    *,
    event_name: str,
    minute: int,
    call_type: str = "1",
    failed_code: str = "",
) -> None:
    data = {
        "CALL_ID": "call-1",
        "USER_ID": "55",
        "CALL_TYPE": call_type,
        "CRM_ACTIVITY_ID": "900",
    }

    if failed_code:
        data["CALL_FAILED_CODE"] = failed_code

    payload = {
        "event": event_name,
        "event_handler_id": "901",
        "data": data,
        "ts": str(
            int(
                dt(
                    6,
                    minute,
                ).timestamp()
            )
        ),
        "auth": {
            "application_token": TOKEN,
            "member_id": "member",
            "domain": "example.test",
        },
    }

    await ingest_bitrix_event(
        database_path=(config.database_path),
        application_token=TOKEN,
        content_type=("application/json"),
        body=json.dumps(payload).encode("utf-8"),
    )


async def materialize(
    config: Settings,
) -> None:
    results = await process_bitrix_event_batch(
        config,
        limit=20,
    )

    assert all(result.outcome == "completed" for result in results)


async def test_411a5c_new_stage_is_open_before_threshold(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result.evaluation.state is EvaluationState.READY

    assert result.evaluation.verdict is EvaluationVerdict.OPEN

    assert result.evaluation.elapsed_business_seconds == 600


async def test_411a5c_new_stage_attention_at_threshold(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            15,
        ),
    )

    assert result.evaluation.verdict is EvaluationVerdict.ATTENTION

    assert result.evaluation.elapsed_business_seconds == 900


async def test_411a5c_outbound_call_resets_timer(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    await add_call_activity(config)

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        minute=10,
        call_type="1",
    )

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        minute=11,
        call_type="1",
        failed_code="200",
    )

    await materialize(config)

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.verdict is EvaluationVerdict.OPEN

    assert result.last_qualifying_activity_kind == "outbound_call"

    assert result.evaluation.elapsed_business_seconds == 600


async def test_411a5c_inbound_call_maps_correctly(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    await add_call_activity(config)

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        minute=10,
        call_type="2",
    )

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        minute=11,
        call_type="2",
        failed_code="200",
    )

    await materialize(config)

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.last_qualifying_activity_kind == "inbound_call"


async def test_411a5c_openlines_message_resets_timer(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    add_manager_message(
        config.database_path,
        minute=10,
    )

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.verdict is EvaluationVerdict.OPEN

    assert result.last_qualifying_activity_kind == "message_to_client"

    assert result.last_qualifying_activity_source == "openlines_message"


async def test_411a5c_outbound_completed_email_resets_timer(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    await add_email(
        config,
        activity_id="800",
        minute=10,
        direction="2",
    )

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.verdict is EvaluationVerdict.OPEN

    assert result.last_qualifying_activity_kind == "message_to_client"

    assert result.last_qualifying_activity_source == "crm_activity_email"


async def test_411a5c_incoming_email_does_not_reset_manager_timer(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    await add_email(
        config,
        activity_id="801",
        minute=10,
        direction="1",
    )

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            16,
        ),
    )

    assert result.evaluation.verdict is EvaluationVerdict.ATTENTION

    assert result.last_qualifying_activity_kind == ""


async def test_411a5c_crm_coverage_gap_blocks(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(
        config,
        crm_start=dt(
            6,
            5,
        ),
    )

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.BLOCKED

    assert "source_coverage_gap:crm_realtime" in result.evaluation.reasons


async def test_411a5c_successful_call_without_start_blocks(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    await complete_coverage(config)

    await add_call_activity(config)

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        minute=11,
        call_type="1",
        failed_code="200",
    )

    await materialize(config)

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.BLOCKED

    assert (
        "relevant_successful_call_missing_valid_exact_evidence:call-1" in result.evaluation.reasons
    )


async def test_411a5c_unbound_stage_is_not_applicable(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(
        config,
        stage_id=("C7:UC_BNE980"),
    )

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.NOT_APPLICABLE

    assert result.evaluation.verdict is EvaluationVerdict.NOT_APPLICABLE


async def test_411a5c_potential_client_remains_blocked(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(
        config,
        stage_id=("C7:FINAL_INVOICE"),
    )

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.BLOCKED

    assert "return_to_client_field_not_bound" in result.evaluation.reasons


async def test_411a5c_moved_time_fallback_is_supported(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(
        config,
        history=False,
    )

    await complete_coverage(config)

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result.stage_entry_source == "crm_deal_moved_time"

    assert result.evaluation.verdict is EvaluationVerdict.OPEN


async def test_411a5c_latest_reentry_history_is_used(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_deal(config)

    store = CrmStore(config.database_path)

    await store.upsert_entities(
        "deal_stage_history",
        [
            {
                "ID": "701",
                "OWNER_ID": "200",
                "STAGE_ID": "C7:PREPARATION",
                "CREATED_TIME": dt(
                    6,
                    2,
                ).isoformat(),
            },
            {
                "ID": "702",
                "OWNER_ID": "200",
                "STAGE_ID": "C7:NEW",
                "CREATED_TIME": dt(
                    6,
                    5,
                ).isoformat(),
            },
        ],
        modified_field=("CREATED_TIME"),
    )

    await complete_coverage(config)

    result = await evaluate_realtime_deal_stage_sla(
        config.database_path,
        deal_id=200,
        as_of=dt(
            6,
            10,
        ),
    )

    assert result.evaluation.anchor_at == dt(
        6,
        5,
    )

    assert result.evaluation.elapsed_business_seconds == 300
