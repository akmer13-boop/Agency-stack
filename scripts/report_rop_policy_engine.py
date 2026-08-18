from __future__ import annotations

from app.services.rop_policy_engine import (
    load_policy_contract,
    policy_engine_readiness,
    qualifying_activity,
)


def main() -> None:
    contract = load_policy_contract()

    results = policy_engine_readiness(contract)

    print("AGENCY STACK - STAGE 4.10C POLICY ENGINE")
    print()

    for key, item in results.items():
        print(f"{key:24} state={item.state.value:14} verdict={item.verdict.value:14}")

        if item.threshold_seconds is not None:
            print(
                "  threshold_seconds:",
                item.threshold_seconds,
            )

        if item.reasons:
            print(
                "  blockers:",
                ", ".join(item.reasons),
            )

    print()
    print(
        "qualifying_activity:",
        ", ".join(qualifying_activity(contract)),
    )

    print()
    print("CRM writes  : NONE")
    print("Bitrix calls: NONE")
    print("SQLite      : NONE")


if __name__ == "__main__":
    main()
