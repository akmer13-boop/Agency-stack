from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.rop_business_time import (
    add_business_seconds,
    evaluate_stage_timer,
    stage_threshold_business_seconds,
)

ZONE = ZoneInfo("Europe/Moscow")


def moment(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=ZONE,
    )


def main() -> None:
    print("AGENCY STACK - STAGE 4.10E BUSINESS TIME CALCULATOR")
    print()

    first = add_business_seconds(
        moment(
            2026,
            8,
            14,
            18,
            55,
        ),
        900,
    )

    print(
        "Friday 18:55 + 15 business min:",
        first.isoformat(),
    )

    holiday = add_business_seconds(
        moment(
            2026,
            2,
            20,
            18,
            55,
        ),
        900,
    )

    print(
        "Before Feb holiday + 15 min:",
        holiday.isoformat(),
    )

    print()

    for stage_id in (
        "C7:NEW",
        "C7:PREPARATION",
        "C7:PREPAYMENT_INVOICE",
        "C7:UC_IAVLST",
        "C7:EXECUTING",
    ):
        print(
            stage_id,
            stage_threshold_business_seconds(stage_id),
        )

    example = evaluate_stage_timer(
        stage_id="C7:PREPARATION",
        stage_entered_at=moment(
            2026,
            8,
            17,
            10,
        ),
        as_of=moment(
            2026,
            8,
            20,
            10,
        ),
    )

    print()
    print(
        "3-business-day stage deadline:",
        example.deadline_at.isoformat(),
    )

    print("CRM writes  : NONE")
    print("Bitrix calls: NONE")
    print("SQLite      : NONE")


if __name__ == "__main__":
    main()
