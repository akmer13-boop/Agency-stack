from __future__ import annotations

import argparse
import asyncio

from app.config import Settings
from app.services.rop_sla_operational_runner import (
    run_operational_sla_cycle,
)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Run one guarded local ROP SLA operational cycle.")
    )

    parser.add_argument(
        "--event-limit",
        type=int,
        default=100,
    )

    parser.add_argument(
        "--deadline-limit",
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

    result = await run_operational_sla_cycle(
        settings.database_path,
        event_limit=args.event_limit,
        deadline_limit=args.deadline_limit,
        max_attempts=args.max_attempts,
    )

    if not result.ready:
        print(
            "ROP_SLA_OPERATIONAL_CYCLE "
            "ready=false "
            "events=0 deadlines=0 "
            "missing="
            + ",".join(result.missing_sources)
            + " lagging="
            + ",".join(result.lagging_sources)
        )
        return

    print(
        "ROP_SLA_OPERATIONAL_CYCLE "
        "ready=true "
        f"events={result.events_processed} "
        f"event_failures={result.event_failures} "
        f"evaluations={result.evaluations_written} "
        f"deadlines={result.deadlines_processed} "
        f"deadline_failures={result.deadline_failures}"
    )


if __name__ == "__main__":
    asyncio.run(main())
