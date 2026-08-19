from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from app.config import Settings
from app.services.rop_sla_deadline_sweep import (
    process_sla_deadline_batch,
)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Re-evaluate due ROP SLA deadlines without requiring a new Bitrix event.")
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=100,
    )

    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
    )

    args = parser.parse_args()

    settings = Settings()

    results = await process_sla_deadline_batch(
        settings.database_path,
        limit=args.limit,
        max_attempts=args.max_attempts,
    )

    outcomes = Counter(item.outcome for item in results)

    verdicts = Counter(item.verdict for item in results if item.verdict)

    print(
        "ROP_SLA_DEADLINE_SWEEP "
        f"processed={len(results)} "
        f"completed={outcomes.get('completed', 0)} "
        f"failed={outcomes.get('failed', 0)} "
        f"attention={verdicts.get('attention', 0)} "
        f"blocked={verdicts.get('blocked', 0)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
