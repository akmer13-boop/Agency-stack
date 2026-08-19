from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.services.bitrix_event_processor import (
    process_bitrix_event_batch,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Process queued Bitrix realtime events using read-only Bitrix API access.")
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
    )

    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be positive")

    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be positive")

    settings = get_settings()

    results = await process_bitrix_event_batch(
        settings,
        limit=args.limit,
        max_attempts=args.max_attempts,
    )

    completed = sum(1 for result in results if result.outcome == "completed")

    failed = sum(1 for result in results if result.outcome == "failed")

    store = BitrixEventInboxStore(settings.database_path)

    counts = await store.count_by_status()

    print("BITRIX REALTIME EVENT PROCESSOR")

    print(
        "processed :",
        len(results),
    )

    print(
        "completed :",
        completed,
    )

    print(
        "failed    :",
        failed,
    )

    print()
    print(
        "inbox pending    :",
        counts.pending,
    )

    print(
        "inbox processing :",
        counts.processing,
    )

    print(
        "inbox completed  :",
        counts.completed,
    )

    print(
        "inbox failed     :",
        counts.failed,
    )

    print()
    print("Remote Bitrix writes: NONE")

    print("OpenAI calls        : NONE")


if __name__ == "__main__":
    asyncio.run(main())
