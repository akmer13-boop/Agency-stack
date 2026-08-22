from __future__ import annotations

import argparse

from app.config import Settings
from app.services.rop_b2c_problem_cards import (
    build_and_format_b2c_problem_cards,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview exact read-only B2C problem cards.",
    )
    parser.add_argument(
        "--scope",
        choices=("leads", "deals", "all"),
        default="all",
    )
    parser.add_argument("--manager-id")
    parser.add_argument(
        "--max-managers",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--cards-per-manager",
        type=int,
        default=3,
    )
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    settings = Settings()

    if settings.allow_crm_write:
        raise RuntimeError("ALLOW_CRM_WRITE must be false")

    print(
        build_and_format_b2c_problem_cards(
            settings,
            scope=arguments.scope,
            manager_id=arguments.manager_id,
            max_managers=arguments.max_managers,
            cards_per_manager=arguments.cards_per_manager,
        )
    )


if __name__ == "__main__":
    main()
