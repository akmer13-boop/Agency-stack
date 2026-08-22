from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.rop_calendar_report import build_rop_calendar_report
from app.services.rop_scheduler import RopSchedulerJobKind


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview a deterministic calendar ROP report.",
    )
    parser.add_argument(
        "kind",
        choices=("daily", "weekly"),
    )
    parser.add_argument(
        "--scheduled-date",
        help=(
            "Local scheduler date in YYYY-MM-DD. "
            "Defaults to today in ROP_TIMEZONE."
        ),
    )
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    settings = Settings()
    if settings.allow_crm_write:
        raise RuntimeError("ALLOW_CRM_WRITE must be false")

    timezone = ZoneInfo(settings.rop_timezone)
    if arguments.scheduled_date:
        scheduled_date = datetime.strptime(
            arguments.scheduled_date,
            "%Y-%m-%d",
        ).date()
    else:
        scheduled_date = datetime.now(timezone).date()

    kind = RopSchedulerJobKind(arguments.kind)
    if kind is RopSchedulerJobKind.DAILY:
        period_key = scheduled_date.isoformat()
    else:
        iso = scheduled_date.isocalendar()
        period_key = f"{iso.year}-W{iso.week:02d}"

    print(
        build_rop_calendar_report(
            settings,
            kind=kind,
            period_key=period_key,
        )
    )


if __name__ == "__main__":
    main()
