from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from app.config import Settings
from app.services.rop_realtime_sla_orchestrator import (
    process_realtime_sla_batch,
)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Process completed Bitrix events through the local ROP SLA orchestrator.")
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

    args = parser.parse_args()

    settings = Settings()

    results = await process_realtime_sla_batch(
        settings.database_path,
        limit=args.limit,
        max_attempts=args.max_attempts,
    )

    outcomes = Counter(item.outcome for item in results)

    evaluations = sum(item.evaluations_written for item in results)

    print(
        "ROP_SLA_DISPATCH "
        f"processed={len(results)} "
        f"completed={outcomes.get('completed', 0)} "
        f"failed={outcomes.get('failed', 0)} "
        f"evaluations={evaluations}"
    )


if __name__ == "__main__":
    asyncio.run(main())
