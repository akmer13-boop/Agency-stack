from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.rop_policy_evaluation import (
    EvidenceRef,
    FirstResponseCase,
    StageTimerCase,
    evaluate_first_response_case,
    evaluate_stage_timer_case,
    format_policy_evaluation_for_ai,
)

ZONE = ZoneInfo("Europe/Moscow")


def dt(
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
    first = evaluate_first_response_case(
        FirstResponseCase(
            lead_id=1001,
            lead_created_at=dt(
                2026,
                8,
                18,
                10,
                2,
            ),
            manager_response_at=dt(
                2026,
                8,
                18,
                10,
                11,
            ),
            lead_created_evidence=EvidenceRef(
                source_type="crm_lead",
                source_id="1001",
                occurred_at=dt(
                    2026,
                    8,
                    18,
                    10,
                    2,
                ),
                event_kind="lead_created",
            ),
            manager_response_evidence=EvidenceRef(
                source_type="openlines_message",
                source_id="msg-501",
                occurred_at=dt(
                    2026,
                    8,
                    18,
                    10,
                    11,
                ),
                event_kind="manager_response",
                actor_kind="directory_user",
                actor_id=77,
            ),
        )
    )

    print("===== FIRST RESPONSE =====")

    print(format_policy_evaluation_for_ai(first))

    stage = evaluate_stage_timer_case(
        StageTimerCase(
            deal_id=2001,
            stage_id="C7:PREPARATION",
            stage_entered_at=dt(
                2026,
                8,
                17,
                10,
            ),
            as_of=dt(
                2026,
                8,
                20,
                9,
                59,
            ),
            stage_entry_evidence=EvidenceRef(
                source_type="crm_stage_history",
                source_id="stage-900",
                occurred_at=dt(
                    2026,
                    8,
                    17,
                    10,
                ),
                event_kind="stage_entered",
            ),
        )
    )

    print()
    print("===== STAGE TIMER =====")

    print(format_policy_evaluation_for_ai(stage))

    print()
    print("CRM writes  : NONE")
    print("Bitrix calls: NONE")
    print("SQLite      : NONE")
    print("OpenAI      : NONE")


if __name__ == "__main__":
    main()
