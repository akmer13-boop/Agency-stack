from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.integrations.bitrix24.sync_client import (
    SyncBitrix24Client,
)
from app.services.rop_voximplant_reconciliation import (
    reconcile_voximplant_statistics,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)
from app.storage.rop_voximplant_reconciliation_store import (
    RopVoximplantReconciliationStore,
)


class FakeVoximplantClient:
    def __init__(
        self,
    ) -> None:
        self.calls = 0

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert method == (
            "voximplant.statistic.get"
        )

        self.calls += 1

        start = int(
            (params or {}).get(
                "start",
                0,
            )
        )

        if start == 0:
            return {
                "result": [
                    {
                        "ID": "1",
                        "CALL_ID": "call-a",
                        "CALL_START_DATE": (
                            "2026-08-21T10:00:00+00:00"
                        ),
                        "CALL_FAILED_CODE": "200",
                        "CALL_DURATION": "120",
                        "CALL_TYPE": "1",
                        "PORTAL_USER_ID": "55",
                        "CRM_ACTIVITY_ID": "101",
                        "CRM_ENTITY_TYPE": "LEAD",
                        "CRM_ENTITY_ID": "501",
                        "PHONE_NUMBER": (
                            "+79990000001"
                        ),
                    },
                    {
                        "ID": "2",
                        "CALL_ID": "call-b",
                        "CALL_START_DATE": (
                            "2026-08-21T10:10:00+00:00"
                        ),
                        "CALL_FAILED_CODE": "304",
                        "CALL_DURATION": "0",
                        "CALL_TYPE": "2",
                        "PORTAL_USER_ID": "56",
                        "CRM_ACTIVITY_ID": "102",
                        "CRM_ENTITY_TYPE": "DEAL",
                        "CRM_ENTITY_ID": "601",
                        "PHONE_NUMBER": (
                            "+79990000002"
                        ),
                    },
                ],
                "total": 3,
                "next": 50,
            }

        assert start == 50

        return {
            "result": [
                {
                    "ID": "3",
                    "CALL_ID": "call-c",
                    "CALL_START_DATE": (
                        "2026-08-21T10:20:00+00:00"
                    ),
                    "CALL_FAILED_CODE": "200",
                    "CALL_DURATION": "0",
                    "CALL_TYPE": "1",
                    "PORTAL_USER_ID": "57",
                    "CRM_ACTIVITY_ID": "103",
                    "CRM_ENTITY_TYPE": "LEAD",
                    "CRM_ENTITY_ID": "502",
                    "PHONE_NUMBER": (
                        "+79990000003"
                    ),
                },
            ],
            "total": 3,
        }


def insert_call_evidence(
    database_path: Path,
    *,
    call_id: str,
    event_name: str,
    event_ts: int,
    suffix: str,
) -> None:
    connection = sqlite3.connect(
        database_path
    )

    try:
        connection.execute(
            """
            INSERT INTO bitrix_call_evidence (
                event_key,
                inbox_id,
                call_id,
                event_name,
                event_ts
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "event-" + suffix,
                int(suffix),
                call_id,
                event_name,
                event_ts,
            ),
        )

        connection.commit()

    finally:
        connection.close()


@pytest.mark.asyncio
async def test_413a31_reconciles_without_storing_phone_numbers(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "reconciliation.db"
    )

    inbox = BitrixEventInboxStore(
        str(database_path)
    )

    await inbox.initialize()

    insert_call_evidence(
        database_path,
        call_id="call-a",
        event_name=(
            "ONVOXIMPLANTCALLSTART"
        ),
        event_ts=1787306400,
        suffix="1",
    )

    insert_call_evidence(
        database_path,
        call_id="call-a",
        event_name=(
            "ONVOXIMPLANTCALLEND"
        ),
        event_ts=1787306500,
        suffix="2",
    )

    insert_call_evidence(
        database_path,
        call_id="call-b",
        event_name=(
            "ONVOXIMPLANTCALLEND"
        ),
        event_ts=1787307000,
        suffix="3",
    )

    insert_call_evidence(
        database_path,
        call_id="call-c",
        event_name=(
            "ONVOXIMPLANTCALLEND"
        ),
        event_ts=1787307600,
        suffix="4",
    )

    client = FakeVoximplantClient()

    result = (
        await reconcile_voximplant_statistics(
            str(database_path),
            client,
            window_start=datetime(
                2026,
                8,
                21,
                9,
                0,
                tzinfo=UTC,
            ),
            window_end=datetime(
                2026,
                8,
                21,
                12,
                0,
                tzinfo=UTC,
            ),
        )
    )

    assert client.calls == 2

    assert result.api_total == 3
    assert result.fetched_rows == 3
    assert result.unique_statistic_ids == 3
    assert result.unique_call_ids == 3

    assert result.successful_calls == 2
    assert result.successful_with_duration == 1
    assert result.policy_candidate_calls == 1
    assert result.crm_linked_calls == 3

    assert result.end_event_matches == 3
    assert result.missing_end_events == 0

    assert result.successful_start_matches == 1
    assert (
        result.successful_missing_start_events
        == 1
    )

    assert result.pagination_complete is True
    assert result.realtime_complete is False

    connection = sqlite3.connect(
        database_path
    )

    try:
        columns = {
            row[1]
            for row in connection.execute(
                """
                PRAGMA table_info(
                    rop_voximplant_statistic_facts
                )
                """
            )
        }

        assert not any(
            "phone"
            in column.lower()
            for column in columns
        )

        rows = connection.execute(
            """
            SELECT
                statistic_id,
                call_id,
                crm_activity_id,
                crm_entity_type,
                crm_entity_id,
                portal_user_id,
                call_type
            FROM rop_voximplant_statistic_facts
            ORDER BY statistic_id
            """
        ).fetchall()

        assert rows == [
            (
                "1",
                "call-a",
                "101",
                "LEAD",
                "501",
                "55",
                "1",
            ),
            (
                "2",
                "call-b",
                "102",
                "DEAL",
                "601",
                "56",
                "2",
            ),
            (
                "3",
                "call-c",
                "103",
                "LEAD",
                "502",
                "57",
                "1",
            ),
        ]

        coverage = connection.execute(
            """
            SELECT
                window_start,
                window_end,
                last_run_id
            FROM rop_voximplant_coverage
            WHERE source_key = 'voximplant_statistics'
            """
        ).fetchone()

        assert coverage == (
            "2026-08-21T09:00:00+00:00",
            "2026-08-21T12:00:00+00:00",
            result.run_id,
        )

    finally:
        connection.close()


def test_413a31_sync_client_allows_voximplant_statistics() -> None:
    client = SyncBitrix24Client(
        "https://example.bitrix24.ru/rest/1/abcdefgh/"
    )

    endpoint = client._endpoint(
        "voximplant.statistic.get"
    )

    assert endpoint.endswith(
        "/voximplant.statistic.get.json"
    )


@pytest.mark.asyncio
async def test_m4_6_legacy_runs_do_not_skip_required_backfill(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE rop_voximplant_reconciliation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            api_total INTEGER NOT NULL,
            fetched_rows INTEGER NOT NULL,
            unique_statistic_ids INTEGER NOT NULL,
            unique_call_ids INTEGER NOT NULL,
            successful_calls INTEGER NOT NULL,
            successful_with_duration INTEGER NOT NULL,
            crm_linked_calls INTEGER NOT NULL,
            end_event_matches INTEGER NOT NULL,
            missing_end_events INTEGER NOT NULL,
            successful_start_matches INTEGER NOT NULL,
            successful_missing_start_events INTEGER NOT NULL,
            orphan_start_events INTEGER NOT NULL,
            orphan_end_events INTEGER NOT NULL,
            pagination_complete INTEGER NOT NULL,
            realtime_complete INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO rop_voximplant_reconciliation_runs (
            window_start,
            window_end,
            api_total,
            fetched_rows,
            unique_statistic_ids,
            unique_call_ids,
            successful_calls,
            successful_with_duration,
            crm_linked_calls,
            end_event_matches,
            missing_end_events,
            successful_start_matches,
            successful_missing_start_events,
            orphan_start_events,
            orphan_end_events,
            pagination_complete,
            realtime_complete
        )
        VALUES (
            '2026-08-01T00:00:00+00:00',
            '2026-08-20T00:00:00+00:00',
            1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0
        )
        """
    )
    connection.commit()
    connection.close()

    store = RopVoximplantReconciliationStore(str(database_path))
    await store.initialize()

    # Old facts lack the M4.6 user/type evidence. They must not establish
    # incremental coverage, otherwise the one-time 120-day refetch is skipped.
    assert await store.get_coverage() is None
