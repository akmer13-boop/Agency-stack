from __future__ import annotations

from app.config import Settings
from app.services.rop_b2c_stage_sla_truth import (
    STAGE_LABELS,
    build_b2c_stage_sla_truth,
)


BLOCKER_LABELS = {
    "return_to_client_date_not_configured": (
        "Не настроено поле даты возврата к клиенту"
    ),
    "stage_entry_evidence_missing_at_cutoff": (
        "Недостаточно данных о входе в стадию"
    ),
    "current_stage_not_valid_at_cutoff": (
        "Стадия изменилась после контрольного среза"
    ),
    "call_coverage_missing_for_attention": (
        "Недостаточно телефонных данных для подтверждения"
    ),
    "successful_call_exact_reset_missing": (
        "Есть успешный звонок, но нельзя безопасно определить сброс SLA"
    ),
}


def main() -> None:
    settings = Settings()

    if settings.allow_crm_write:
        raise RuntimeError(
            "ALLOW_CRM_WRITE must be false"
        )

    report = build_b2c_stage_sla_truth(
        settings.database_path
    )

    print(
        "===== B2C STAGE SLA TRUTH MVP ====="
    )
    print("CUTOFF_UTC =", report.cutoff_at.isoformat())
    print("CRM_RUN_ID =", report.crm_run_id)
    print(
        "OPENLINES_LAST_UTC =",
        (
            report.openlines_last_at.isoformat()
            if report.openlines_last_at
            else "NONE"
        ),
    )
    print("VOX_RUN_ID =", report.vox_run_id)
    print()

    print("TRACKED_B2C_DEALS =", report.tracked_deals)
    print("OPEN =", report.open)
    print("ATTENTION =", report.attention)
    print("BLOCKED =", report.blocked)
    print()

    print("===== BY STAGE =====")
    for (
        stage_id,
        total,
        open_count,
        attention_count,
        blocked_count,
    ) in report.by_stage:
        print(
            f"{stage_id} | "
            f"{STAGE_LABELS.get(stage_id, stage_id)} | "
            f"total={total} | "
            f"open={open_count} | "
            f"attention={attention_count} | "
            f"blocked={blocked_count}"
        )

    print()
    print("===== BLOCKED REASONS =====")
    if not report.blocked_reasons:
        print("NONE")
    for reason, count in report.blocked_reasons:
        print(
            f"{BLOCKER_LABELS.get(reason, reason)} = {count}"
        )

    print()
    print("===== TOP MANAGERS BY ATTENTION =====")
    if not report.attention_by_manager:
        print("NONE")
    for (
        _manager_id,
        manager_name,
        count,
    ) in report.attention_by_manager[:15]:
        print(f"{manager_name} = {count}")

    print()
    print("===== ATTENTION SAMPLE =====")
    sample = [
        item
        for item in report.deals
        if item.status == "ATTENTION"
    ][:20]

    if not sample:
        print("NONE")

    for item in sample:
        print(
            f"deal={item.deal_id} | "
            f"{item.stage_label} | "
            f"{item.manager_name} | "
            f"deadline={item.deadline_at.isoformat() if item.deadline_at else 'NONE'}"
        )

    print()
    print("SEMANTICS = OPEN / ATTENTION / BLOCKED")
    print("BLOCKED IS NOT A VIOLATION")
    print("PHONE NUMBERS EXPOSED = NO")
    print("CUSTOMER TEXT EXPOSED = NO")
    print("BITRIX WRITES = NONE")


if __name__ == "__main__":
    main()
