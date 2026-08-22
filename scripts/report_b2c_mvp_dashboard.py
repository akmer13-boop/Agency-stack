from __future__ import annotations

from app.config import Settings
from app.services.rop_b2c_mvp_dashboard import (
    build_b2c_mvp_dashboard,
    format_b2c_mvp_dashboard,
)


def main() -> None:
    settings = Settings()

    if settings.allow_crm_write:
        raise RuntimeError(
            "ALLOW_CRM_WRITE must be false"
        )

    dashboard = build_b2c_mvp_dashboard(
        settings
    )

    print(
        format_b2c_mvp_dashboard(
            dashboard
        )
    )
    print()
    print("BITRIX WRITES = NONE")


if __name__ == "__main__":
    main()
