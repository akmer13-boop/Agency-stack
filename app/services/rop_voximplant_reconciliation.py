from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import aiosqlite

from app.storage.rop_voximplant_reconciliation_store import (
    RopVoximplantReconciliationStore,
    VoximplantStatisticFact,
)


class VoximplantStatisticClient(Protocol):
    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class VoximplantReconciliationResult:
    run_id: int
    api_total: int
    fetched_rows: int
    unique_statistic_ids: int
    unique_call_ids: int
    successful_calls: int
    successful_with_duration: int
    policy_candidate_calls: int
    crm_linked_calls: int
    end_event_matches: int
    missing_end_events: int
    successful_start_matches: int
    successful_missing_start_events: int
    orphan_start_events: int
    orphan_end_events: int
    pagination_complete: bool
    realtime_complete: bool


def _aware(
    value: datetime,
) -> datetime:
    if value.tzinfo is None:
        raise ValueError(
            "voximplant_window_timezone_missing"
        )

    return value.astimezone(UTC)


def _text(
    value: Any,
) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _duration(
    value: Any,
) -> int | None:
    if value in (
        None,
        "",
    ):
        return None

    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "voximplant_duration_invalid"
        ) from exc

    if result < 0:
        raise ValueError(
            "voximplant_duration_invalid"
        )

    return result


def _sanitize(
    row: dict[str, Any],
) -> VoximplantStatisticFact:
    statistic_id = _text(
        row.get("ID")
    )

    call_id = _text(
        row.get("CALL_ID")
    )

    call_start = _text(
        row.get("CALL_START_DATE")
    )

    if (
        not statistic_id
        or not call_id
        or not call_start
    ):
        raise ValueError(
            "voximplant_statistic_row_invalid"
        )

    try:
        parsed_start = datetime.fromisoformat(
            call_start.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError as exc:
        raise ValueError(
            "voximplant_call_start_invalid"
        ) from exc

    if parsed_start.tzinfo is None:
        raise ValueError(
            "voximplant_call_start_timezone_missing"
        )

    return VoximplantStatisticFact(
        statistic_id=statistic_id,
        call_id=call_id,
        call_start_at=(
            parsed_start.astimezone(
                UTC
            ).isoformat()
        ),
        call_failed_code=_text(
            row.get(
                "CALL_FAILED_CODE"
            )
        ),
        call_duration_seconds=_duration(
            row.get(
                "CALL_DURATION"
            )
        ),
        crm_activity_id=_text(
            row.get(
                "CRM_ACTIVITY_ID"
            )
        ),
        crm_entity_type=_text(
            row.get(
                "CRM_ENTITY_TYPE"
            )
        ).upper(),
        crm_entity_id=_text(
            row.get(
                "CRM_ENTITY_ID"
            )
        ),
        portal_user_id=_text(
            row.get(
                "PORTAL_USER_ID"
            )
        ),
        call_type=_text(
            row.get(
                "CALL_TYPE"
            )
        ),
    )


async def _fetch_all(
    client: VoximplantStatisticClient,
    *,
    window_start: datetime,
    window_end: datetime,
    max_pages: int,
) -> tuple[
    tuple[VoximplantStatisticFact, ...],
    int,
]:
    if max_pages < 1:
        raise ValueError(
            "voximplant_max_pages_invalid"
        )

    start = 0
    api_total: int | None = None

    facts: list[
        VoximplantStatisticFact
    ] = []

    for _ in range(
        max_pages
    ):
        response = await client.call(
            "voximplant.statistic.get",
            {
                "FILTER": {
                    ">=CALL_START_DATE": (
                        window_start.isoformat()
                    ),
                    "<=CALL_START_DATE": (
                        window_end.isoformat()
                    ),
                },
                "SORT": "ID",
                "ORDER": "ASC",
                "start": start,
            },
        )

        result = response.get(
            "result"
        )

        if not isinstance(
            result,
            list,
        ):
            raise ValueError(
                "voximplant_result_invalid"
            )

        for row in result:
            if not isinstance(
                row,
                dict,
            ):
                raise ValueError(
                    "voximplant_row_invalid"
                )

            facts.append(
                _sanitize(row)
            )

        raw_total = response.get(
            "total"
        )

        try:
            current_total = int(
                raw_total
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "voximplant_total_invalid"
            ) from exc

        if api_total is None:
            api_total = current_total

        elif api_total != current_total:
            raise ValueError(
                "voximplant_total_changed_during_snapshot"
            )

        next_start = response.get(
            "next"
        )

        if next_start is None:
            return (
                tuple(facts),
                api_total,
            )

        try:
            next_value = int(
                next_start
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "voximplant_next_invalid"
            ) from exc

        if next_value <= start:
            raise ValueError(
                "voximplant_pagination_not_advancing"
            )

        start = next_value

    raise ValueError(
        "voximplant_pagination_safety_limit"
    )


async def _event_evidence(
    database_path: str,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[
    str,
    set[str],
]:
    async with aiosqlite.connect(
        database_path
    ) as database:
        cursor = await database.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE
                type = 'table'
                AND name = 'bitrix_call_evidence'
            LIMIT 1
            """
        )

        if await cursor.fetchone() is None:
            return {}

        start_ts = int(
            window_start.timestamp()
        )

        end_ts = int(
            window_end.timestamp()
        )

        cursor = await database.execute(
            """
            SELECT
                call_id,
                event_name
            FROM bitrix_call_evidence
            WHERE event_ts >= ?
              AND event_ts <= ?
              AND call_id IS NOT NULL
              AND call_id <> ''
            """,
            (
                start_ts,
                end_ts,
            ),
        )

        rows = await cursor.fetchall()

    result: dict[
        str,
        set[str],
    ] = {}

    for (
        call_id,
        event_name,
    ) in rows:
        key = str(
            call_id
        )

        result.setdefault(
            key,
            set(),
        ).add(
            str(
                event_name
            )
        )

    return result


async def reconcile_voximplant_statistics(
    database_path: str,
    client: VoximplantStatisticClient,
    *,
    window_start: datetime,
    window_end: datetime,
    max_pages: int = 100,
) -> VoximplantReconciliationResult:
    start = _aware(
        window_start
    )

    end = _aware(
        window_end
    )

    if end <= start:
        raise ValueError(
            "voximplant_window_invalid"
        )

    (
        facts,
        api_total,
    ) = await _fetch_all(
        client,
        window_start=start,
        window_end=end,
        max_pages=max_pages,
    )

    statistic_ids = {
        item.statistic_id
        for item in facts
    }

    call_ids = {
        item.call_id
        for item in facts
    }

    successful = [
        item
        for item in facts
        if item.call_failed_code
        == "200"
    ]

    successful_with_duration = sum(
        (
            item.call_duration_seconds
            or 0
        )
        > 0
        for item in successful
    )

    policy_candidate_calls = sum(
        item.call_failed_code == "200"
        and (item.call_duration_seconds or 0) > 0
        and item.call_type in {"1", "2"}
        and bool(item.portal_user_id)
        and bool(
            item.crm_activity_id
            or (
                item.crm_entity_type
                and item.crm_entity_id
            )
        )
        for item in facts
    )

    crm_linked = sum(
        bool(
            item.crm_activity_id
        )
        for item in facts
    )

    events = await _event_evidence(
        database_path,
        window_start=start,
        window_end=end,
    )

    end_matches = sum(
        (
            "ONVOXIMPLANTCALLEND"
            in events.get(
                item.call_id,
                set(),
            )
        )
        for item in facts
    )

    successful_start_matches = sum(
        (
            "ONVOXIMPLANTCALLSTART"
            in events.get(
                item.call_id,
                set(),
            )
        )
        for item in successful
    )

    missing_end = (
        len(facts)
        - end_matches
    )

    missing_success_start = (
        len(successful)
        - successful_start_matches
    )

    statistic_call_ids = {
        item.call_id
        for item in facts
    }

    orphan_start = sum(
        (
            call_id
            not in statistic_call_ids
            and "ONVOXIMPLANTCALLSTART"
            in names
        )
        for (
            call_id,
            names,
        ) in events.items()
    )

    orphan_end = sum(
        (
            call_id
            not in statistic_call_ids
            and "ONVOXIMPLANTCALLEND"
            in names
        )
        for (
            call_id,
            names,
        ) in events.items()
    )

    pagination_complete = (
        len(facts)
        == api_total
        and len(statistic_ids)
        == len(facts)
    )

    realtime_complete = (
        pagination_complete
        and missing_end == 0
        and missing_success_start == 0
    )

    store = (
        RopVoximplantReconciliationStore(
            database_path
        )
    )

    run_id = await store.save(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        api_total=api_total,
        facts=facts,
        unique_statistic_ids=(
            len(statistic_ids)
        ),
        unique_call_ids=(
            len(call_ids)
        ),
        successful_calls=(
            len(successful)
        ),
        successful_with_duration=(
            successful_with_duration
        ),
        policy_candidate_calls=(
            policy_candidate_calls
        ),
        crm_linked_calls=(
            crm_linked
        ),
        end_event_matches=(
            end_matches
        ),
        missing_end_events=(
            missing_end
        ),
        successful_start_matches=(
            successful_start_matches
        ),
        successful_missing_start_events=(
            missing_success_start
        ),
        orphan_start_events=(
            orphan_start
        ),
        orphan_end_events=(
            orphan_end
        ),
        pagination_complete=(
            pagination_complete
        ),
        realtime_complete=(
            realtime_complete
        ),
    )

    return VoximplantReconciliationResult(
        run_id=run_id,
        api_total=api_total,
        fetched_rows=len(facts),
        unique_statistic_ids=(
            len(statistic_ids)
        ),
        unique_call_ids=(
            len(call_ids)
        ),
        successful_calls=(
            len(successful)
        ),
        successful_with_duration=(
            successful_with_duration
        ),
        policy_candidate_calls=(
            policy_candidate_calls
        ),
        crm_linked_calls=(
            crm_linked
        ),
        end_event_matches=(
            end_matches
        ),
        missing_end_events=(
            missing_end
        ),
        successful_start_matches=(
            successful_start_matches
        ),
        successful_missing_start_events=(
            missing_success_start
        ),
        orphan_start_events=(
            orphan_start
        ),
        orphan_end_events=(
            orphan_end
        ),
        pagination_complete=(
            pagination_complete
        ),
        realtime_complete=(
            realtime_complete
        ),
    )
