from __future__ import annotations

import argparse

from app.config import Settings
from app.services.rop_b2c_problem_cards import (
    build_and_format_b2c_today_focus,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview the clean read-only B2C intervention list.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
    )
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    settings = Settings()

    if settings.allow_crm_write:
        raise RuntimeError("ALLOW_CRM_WRITE must be false")

    print(
        build_and_format_b2c_today_focus(
            settings,
            limit=arguments.limit,
        )
    )


if __name__ == "__main__":
    main()
