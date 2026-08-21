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
                        "CRM_ACTIVITY_ID": "101",
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
                        "CRM_ACTIVITY_ID": "102",
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
                    "CRM_ACTIVITY_ID": "103",
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
                crm_activity_id
            FROM rop_voximplant_statistic_facts
            ORDER BY statistic_id
            """
        ).fetchall()

        assert rows == [
            ("1", "call-a", "101"),
            ("2", "call-b", "102"),
            ("3", "call-c", "103"),
        ]

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
