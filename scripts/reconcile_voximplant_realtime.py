from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.services.bitrix24_sync import (
    build_sync_client,
)
from app.services.rop_voximplant_reconciliation import (
    reconcile_voximplant_statistics,
)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile read-only Voximplant statistics "
            "against realtime call events."
        )
    )

    parser.add_argument(
        "--hours",
        type=int,
        default=48,
    )

    parser.add_argument(
        "--settle-minutes",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
    )

    args = parser.parse_args()

    if args.hours < 1:
        raise ValueError(
            "hours must be positive"
        )

    if args.settle_minutes < 0:
        raise ValueError(
            "settle-minutes must not be negative"
        )

    settings = Settings()

    if settings.allow_crm_write:
        raise RuntimeError(
            "ALLOW_CRM_WRITE must be false"
        )

    now = datetime.now(
        UTC
    )

    window_end = (
        now
        - timedelta(
            minutes=args.settle_minutes
        )
    )

    window_start = (
        window_end
        - timedelta(
            hours=args.hours
        )
    )

    client = build_sync_client(
        settings
    )

    result = (
        await reconcile_voximplant_statistics(
            settings.database_path,
            client,
            window_start=window_start,
            window_end=window_end,
            max_pages=args.max_pages,
        )
    )

    print(
        "===== VOXIMPLANT REALTIME RECONCILIATION ====="
    )

    print(
        "RUN_ID =",
        result.run_id,
    )

    print(
        "API_TOTAL =",
        result.api_total,
    )

    print(
        "FETCHED_ROWS =",
        result.fetched_rows,
    )

    print(
        "UNIQUE_STATISTIC_IDS =",
        result.unique_statistic_ids,
    )

    print(
        "UNIQUE_CALL_IDS =",
        result.unique_call_ids,
    )

    print(
        "SUCCESSFUL_CALLS =",
        result.successful_calls,
    )

    print(
        "SUCCESSFUL_WITH_DURATION =",
        result.successful_with_duration,
    )

    print(
        "CRM_LINKED_CALLS =",
        result.crm_linked_calls,
    )

    print(
        "END_EVENT_MATCHES =",
        result.end_event_matches,
    )

    print(
        "MISSING_END_EVENTS =",
        result.missing_end_events,
    )

    print(
        "SUCCESSFUL_START_MATCHES =",
        result.successful_start_matches,
    )

    print(
        "SUCCESSFUL_MISSING_START =",
        result.successful_missing_start_events,
    )

    print(
        "ORPHAN_START_EVENTS =",
        result.orphan_start_events,
    )

    print(
        "ORPHAN_END_EVENTS =",
        result.orphan_end_events,
    )

    print(
        "PAGINATION_COMPLETE =",
        result.pagination_complete,
    )

    print(
        "REALTIME_COMPLETE =",
        result.realtime_complete,
    )

    print(
        "PHONE_NUMBERS_STORED = NO"
    )

    print(
        "BITRIX WRITES = NONE"
    )

    print(
        "COVERAGE_ADVANCED = NO"
    )


if __name__ == "__main__":
    asyncio.run(main())
