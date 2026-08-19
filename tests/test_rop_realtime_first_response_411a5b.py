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
from app.services.rop_realtime_first_response import (
    evaluate_realtime_first_response,
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


async def prepare_database(
    config: Settings,
) -> None:
    crm = CrmStore(config.database_path)

    await crm.initialize()

    await crm.upsert_entities(
        "lead",
        [
            {
                "ID": "100",
                "DATE_CREATE": dt(
                    6,
                    0,
                ).isoformat(),
                "STATUS_ID": "NEW",
            },
        ],
        modified_field=None,
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
    openlines_start: datetime | None = None,
    call_start: datetime | None = None,
) -> None:
    store = RopSourceCoverageStore(config.database_path)

    await store.add_interval(
        source_key="openlines",
        coverage_start=(
            openlines_start
            or dt(
                5,
                0,
            )
        ),
        coverage_end=None,
        evidence_kind=("test_verified_openlines_coverage"),
    )

    await store.add_interval(
        source_key=("voximplant_realtime"),
        coverage_start=(
            call_start
            or dt(
                5,
                0,
            )
        ),
        coverage_end=None,
        evidence_kind=("test_verified_realtime_call_coverage"),
    )


async def ingest_call(
    config: Settings,
    *,
    event_name: str,
    minute: int,
    failed_code: str = "",
) -> None:
    data = {
        "CALL_ID": "call-1",
        "USER_ID": "55",
        "CALL_TYPE": "1",
        "CRM_ACTIVITY_ID": "900",
        "CRM_ENTITY_TYPE": "LEAD",
        "CRM_ENTITY_ID": "100",
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


async def materialize_calls(
    config: Settings,
) -> None:
    results = await process_bitrix_event_batch(
        config,
        limit=20,
    )

    assert all(result.outcome == "completed" for result in results)


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
            'lead',
            '100'
        )
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


async def test_411a5b_exact_realtime_call_evaluates_ok(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    await complete_coverage(config)

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        minute=5,
    )

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        minute=6,
        failed_code="200",
    )

    await materialize_calls(config)

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.READY

    assert result.evaluation.verdict is EvaluationVerdict.OK

    assert result.exact_response_source == "voximplant_call_start_event"

    assert result.evaluation.elapsed_business_seconds == 300


async def test_411a5b_complete_sources_no_response_breaches(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    await complete_coverage(config)

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            16,
        ),
    )

    assert result.evaluation.state is EvaluationState.READY

    assert result.evaluation.verdict is EvaluationVerdict.BREACH

    assert result.exact_response_source == "no_response_observed"


async def test_411a5b_incomplete_openlines_blocks(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    coverage = RopSourceCoverageStore(config.database_path)

    await coverage.add_interval(
        source_key=("voximplant_realtime"),
        coverage_start=dt(
            5,
            0,
        ),
        coverage_end=None,
        evidence_kind="verified",
    )

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.BLOCKED

    assert "source_coverage_missing:openlines" in result.evaluation.reasons


async def test_411a5b_call_coverage_start_after_lead_blocks(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    await complete_coverage(
        config,
        call_start=dt(
            6,
            5,
        ),
    )

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.BLOCKED

    assert "source_coverage_gap:voximplant_realtime" in result.evaluation.reasons


async def test_411a5b_relevant_success_without_start_blocks(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    await complete_coverage(config)

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        minute=6,
        failed_code="200",
    )

    await materialize_calls(config)

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.BLOCKED

    assert (
        "relevant_successful_call_missing_valid_exact_evidence:call-1" in result.evaluation.reasons
    )


async def test_411a5b_failed_call_does_not_count_as_response(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    await complete_coverage(config)

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLSTART"),
        minute=5,
    )

    await ingest_call(
        config,
        event_name=("ONVOXIMPLANTCALLEND"),
        minute=6,
        failed_code="304",
    )

    await materialize_calls(config)

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            16,
        ),
    )

    assert result.evaluation.verdict is EvaluationVerdict.BREACH

    assert result.relevant_successful_call_ids == ()


async def test_411a5b_openlines_message_evaluates_ok(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    await complete_coverage(config)

    add_manager_message(
        config.database_path,
        minute=5,
    )

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.READY

    assert result.evaluation.verdict is EvaluationVerdict.OK

    assert result.exact_response_source == "openlines_message"


async def test_411a5b_coverage_gap_between_intervals_blocks(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await prepare_database(config)

    coverage = RopSourceCoverageStore(config.database_path)

    await coverage.add_interval(
        source_key="openlines",
        coverage_start=dt(
            5,
            0,
        ),
        coverage_end=None,
        evidence_kind="verified",
    )

    await coverage.add_interval(
        source_key=("voximplant_realtime"),
        coverage_start=dt(
            5,
            0,
        ),
        coverage_end=dt(
            6,
            5,
        ),
        evidence_kind="verified-1",
    )

    await coverage.add_interval(
        source_key=("voximplant_realtime"),
        coverage_start=dt(
            6,
            6,
        ),
        coverage_end=None,
        evidence_kind="verified-2",
    )

    result = await evaluate_realtime_first_response(
        config.database_path,
        lead_id=100,
        as_of=dt(
            6,
            20,
        ),
    )

    assert result.evaluation.state is EvaluationState.BLOCKED

    assert "source_coverage_gap:voximplant_realtime" in result.evaluation.reasons
