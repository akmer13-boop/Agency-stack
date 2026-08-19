from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.config import Settings
from app.integrations.bitrix24.client import (
    Bitrix24RequestError,
)
from app.services.bitrix_event_processor import (
    EventEntitySnapshot,
    process_bitrix_event_batch,
    process_next_bitrix_event,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)


class FakeReader:
    def __init__(
        self,
        payloads: dict[
            tuple[str, str],
            dict,
        ]
        | None = None,
        *,
        error: Bitrix24RequestError | None = None,
    ) -> None:
        self.payloads = payloads or {}

        self.error = error

        self.calls: list[tuple[str, str]] = []

    async def fetch(
        self,
        *,
        entity_type: str,
        entity_id: str,
    ) -> EventEntitySnapshot:
        self.calls.append(
            (
                entity_type,
                entity_id,
            )
        )

        if self.error is not None:
            raise self.error

        payload = self.payloads[
            (
                entity_type,
                entity_id,
            )
        ]

        return EventEntitySnapshot(
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )


def settings(
    tmp_path: Path,
) -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "events.db"),
        bitrix24_webhook_url=("https://example.test/rest/1/abcdefgh/"),
    )


async def enqueue(
    config: Settings,
    *,
    key: str,
    event_name: str,
    entity_type: str = "",
    entity_id: str = "",
    call_id: str = "",
    actor_user_id: str = "",
    data: dict | None = None,
) -> int:
    store = BitrixEventInboxStore(config.database_path)

    result = await store.enqueue(
        event_key=key,
        event_name=event_name,
        event_ts=1787120000,
        event_handler_id="1",
        entity_type=entity_type,
        entity_id=entity_id,
        call_id=call_id,
        actor_user_id=actor_user_id,
        member_id="member",
        domain="example.test",
        data_json=json.dumps(
            data or {},
            sort_keys=True,
        ),
    )

    return result.inbox_id


def row(
    database_path: str,
    query: str,
    params: tuple = (),
):
    con = sqlite3.connect(database_path)

    try:
        return con.execute(
            query,
            params,
        ).fetchone()

    finally:
        con.close()


async def test_411a4_claim_moves_pending_to_processing(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="e1",
        event_name="ONCRMLEADUPDATE",
        entity_type="lead",
        entity_id="100",
    )

    store = BitrixEventInboxStore(config.database_path)

    event = await store.claim_next()

    assert event is not None
    assert event.status == "processing"
    assert event.attempts == 1

    stored = row(
        config.database_path,
        """
        SELECT status, attempts
        FROM bitrix_event_inbox
        WHERE id = ?
        """,
        (event.inbox_id,),
    )

    assert stored == (
        "processing",
        1,
    )


async def test_411a4_lead_update_refreshes_local_crm(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="lead-update",
        event_name="ONCRMLEADUPDATE",
        entity_type="lead",
        entity_id="100",
    )

    reader = FakeReader(
        {
            (
                "lead",
                "100",
            ): {
                "ID": "100",
                "STATUS_ID": "NEW",
                "DATE_MODIFY": "2026-08-19T10:00:00+03:00",
            },
        }
    )

    result = await process_next_bitrix_event(
        config,
        reader=reader,
    )

    assert result is not None
    assert result.outcome == "completed"

    stored = row(
        config.database_path,
        """
        SELECT
            entity_type,
            entity_id,
            json_extract(
                payload_json,
                '$.STATUS_ID'
            )
        FROM crm_raw_entities
        WHERE entity_type = 'lead'
          AND entity_id = '100'
        """,
    )

    assert stored == (
        "lead",
        "100",
        "NEW",
    )

    assert reader.calls == [
        (
            "lead",
            "100",
        )
    ]


async def test_411a4_deal_move_refreshes_deal(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="deal-move",
        event_name=("ONCRMDEALMOVETOCATEGORY"),
        entity_type="deal",
        entity_id="200",
    )

    reader = FakeReader(
        {
            (
                "deal",
                "200",
            ): {
                "ID": "200",
                "CATEGORY_ID": "7",
                "STAGE_ID": "C7:EXECUTING",
                "DATE_MODIFY": "2026-08-19T10:00:00+03:00",
            },
        }
    )

    result = await process_next_bitrix_event(
        config,
        reader=reader,
    )

    assert result is not None
    assert result.result_code == "entity_refreshed"

    stored = row(
        config.database_path,
        """
        SELECT json_extract(
            payload_json,
            '$.STAGE_ID'
        )
        FROM crm_raw_entities
        WHERE entity_type = 'deal'
          AND entity_id = '200'
        """,
    )

    assert stored == ("C7:EXECUTING",)


async def test_411a4_activity_update_refreshes_activity(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="activity-update",
        event_name=("ONCRMACTIVITYUPDATE"),
        entity_type="activity",
        entity_id="300",
    )

    reader = FakeReader(
        {
            (
                "activity",
                "300",
            ): {
                "ID": "300",
                "TYPE_ID": "2",
                "LAST_UPDATED": "2026-08-19T10:00:00+03:00",
            },
        }
    )

    result = await process_next_bitrix_event(
        config,
        reader=reader,
    )

    assert result is not None
    assert result.outcome == "completed"

    stored = row(
        config.database_path,
        """
        SELECT json_extract(
            payload_json,
            '$.TYPE_ID'
        )
        FROM crm_raw_entities
        WHERE entity_type = 'activity'
          AND entity_id = '300'
        """,
    )

    assert stored == ("2",)


async def test_411a4_delete_is_observed_not_physically_deleted(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="delete-1",
        event_name="ONCRMDEALDELETE",
        entity_type="deal",
        entity_id="400",
    )

    reader = FakeReader()

    result = await process_next_bitrix_event(
        config,
        reader=reader,
    )

    assert result is not None

    assert result.result_code == "delete_observation_recorded"

    assert reader.calls == []

    observed = row(
        config.database_path,
        """
        SELECT
            entity_type,
            entity_id,
            event_name
        FROM bitrix_entity_delete_observations
        WHERE event_key = 'delete-1'
        """,
    )

    assert observed == (
        "deal",
        "400",
        "ONCRMDEALDELETE",
    )


async def test_411a4_call_start_materializes_exact_evidence(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="call-start",
        event_name=("ONVOXIMPLANTCALLSTART"),
        entity_type="call",
        call_id="call-1",
        actor_user_id="55",
        data={
            "CALL_ID": "call-1",
            "USER_ID": "55",
        },
    )

    reader = FakeReader()

    result = await process_next_bitrix_event(
        config,
        reader=reader,
    )

    assert result is not None

    assert result.result_code == "call_conversation_start_recorded"

    assert reader.calls == []

    evidence = row(
        config.database_path,
        """
        SELECT
            call_id,
            event_name,
            actor_user_id,
            call_failed_code,
            call_duration_seconds
        FROM bitrix_call_evidence
        WHERE event_key = 'call-start'
        """,
    )

    assert evidence == (
        "call-1",
        "ONVOXIMPLANTCALLSTART",
        "55",
        None,
        None,
    )


async def test_411a4_call_end_keeps_safe_outcome_fields(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="call-end",
        event_name=("ONVOXIMPLANTCALLEND"),
        entity_type="call",
        call_id="call-2",
        actor_user_id="77",
        data={
            "CALL_ID": "call-2",
            "USER_ID": "77",
            "CALL_FAILED_CODE": "200",
            "CALL_DURATION": "48",
            "CRM_ACTIVITY_ID": "900",
            "CRM_ENTITY_TYPE": "LEAD",
            "CRM_ENTITY_ID": "100",
            "PHONE_NUMBER": "+00000000000",
        },
    )

    result = await process_next_bitrix_event(
        config,
        reader=FakeReader(),
    )

    assert result is not None
    assert result.outcome == "completed"

    evidence = row(
        config.database_path,
        """
        SELECT
            call_failed_code,
            call_duration_seconds,
            crm_activity_id,
            crm_entity_type,
            crm_entity_id
        FROM bitrix_call_evidence
        WHERE event_key = 'call-end'
        """,
    )

    assert evidence == (
        "200",
        48,
        "900",
        "LEAD",
        "100",
    )

    raw = Path(config.database_path).read_bytes()

    assert b"+00000000000" not in raw

    materialized = row(
        config.database_path,
        """
        SELECT COUNT(*)
        FROM bitrix_call_evidence
        WHERE event_key = 'call-end'
          AND (
              call_id LIKE '%00000000000%'
              OR crm_activity_id
                 LIKE '%00000000000%'
          )
        """,
    )

    assert materialized == (0,)


async def test_411a4_read_failure_marks_event_failed(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    inbox_id = await enqueue(
        config,
        key="failure",
        event_name="ONCRMLEADUPDATE",
        entity_type="lead",
        entity_id="500",
    )

    reader = FakeReader(
        error=Bitrix24RequestError(
            "safe",
            error_code="HTTP_503",
        )
    )

    result = await process_next_bitrix_event(
        config,
        reader=reader,
    )

    assert result is not None
    assert result.outcome == "failed"
    assert result.error_code == "HTTP_503"

    stored = row(
        config.database_path,
        """
        SELECT
            status,
            attempts,
            last_error
        FROM bitrix_event_inbox
        WHERE id = ?
        """,
        (inbox_id,),
    )

    assert stored == (
        "failed",
        1,
        "HTTP_503",
    )


async def test_411a4_failed_event_is_retried(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="retry",
        event_name="ONCRMLEADUPDATE",
        entity_type="lead",
        entity_id="600",
    )

    failing = FakeReader(
        error=Bitrix24RequestError(
            "safe",
            error_code="HTTP_503",
        )
    )

    first = await process_next_bitrix_event(
        config,
        reader=failing,
        max_attempts=3,
    )

    assert first is not None
    assert first.attempts == 1
    assert first.outcome == "failed"

    success = FakeReader(
        {
            (
                "lead",
                "600",
            ): {
                "ID": "600",
                "STATUS_ID": "NEW",
            },
        }
    )

    second = await process_next_bitrix_event(
        config,
        reader=success,
        max_attempts=3,
    )

    assert second is not None
    assert second.attempts == 2
    assert second.outcome == "completed"


async def test_411a4_max_attempts_stops_retry(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="dead",
        event_name="ONCRMLEADUPDATE",
        entity_type="lead",
        entity_id="700",
    )

    failing = FakeReader(
        error=Bitrix24RequestError(
            "safe",
            error_code="HTTP_503",
        )
    )

    for expected in (
        1,
        2,
        3,
    ):
        result = await process_next_bitrix_event(
            config,
            reader=failing,
            max_attempts=3,
        )

        assert result is not None
        assert result.attempts == expected

    stopped = await process_next_bitrix_event(
        config,
        reader=failing,
        max_attempts=3,
    )

    assert stopped is None


async def test_411a4_batch_stops_when_queue_empty(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="batch-call",
        event_name=("ONVOXIMPLANTCALLSTART"),
        entity_type="call",
        call_id="call-batch",
        actor_user_id="55",
        data={
            "CALL_ID": "call-batch",
            "USER_ID": "55",
        },
    )

    results = await process_bitrix_event_batch(
        config,
        reader=FakeReader(),
        limit=10,
    )

    assert len(results) == 1

    assert results[0].outcome == "completed"


async def test_411a4_status_counts(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)

    await enqueue(
        config,
        key="count-1",
        event_name=("ONVOXIMPLANTCALLSTART"),
        entity_type="call",
        call_id="count-call",
        data={
            "CALL_ID": "count-call",
        },
    )

    store = BitrixEventInboxStore(config.database_path)

    before = await store.count_by_status()

    assert before.pending == 1
    assert before.completed == 0

    await process_next_bitrix_event(
        config,
        reader=FakeReader(),
    )

    after = await store.count_by_status()

    assert after.pending == 0
    assert after.completed == 1
    assert after.processing == 0
    assert after.failed == 0
