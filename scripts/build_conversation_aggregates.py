from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.conversation_aggregates import build_conversation_aggregates


async def _main() -> None:
    settings = Settings()
    result = await build_conversation_aggregates(settings.database_path)

    print("=" * 74)
    print(" SAFE CONVERSATION AGGREGATES")
    print("=" * 74)
    print("Global rows              :", result.global_rows)
    print("Manager rows             :", result.manager_rows)
    print("Active manager rows      :", result.active_manager_rows)
    print("Inactive manager rows    :", result.inactive_manager_rows)
    print("Channel rows             :", result.channel_rows)
    print("CRM entity rows          :", result.crm_entity_rows)
    print("CRM event-link rows      :", result.crm_event_link_rows)
    print("=" * 74)


if __name__ == "__main__":
    asyncio.run(_main())
