from __future__ import annotations

import argparse
import asyncio

from app.config import Settings
from app.services.rop_openlines_response import (
    build_openlines_response_report,
    format_openlines_response_for_ai,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Open Lines factual response report.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--manager-id", type=str, default=None)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = Settings()
    report = await build_openlines_response_report(
        settings.database_path,
        args.days,
        manager_id=args.manager_id,
    )
    print(format_openlines_response_for_ai(report))


if __name__ == "__main__":
    asyncio.run(_main())
