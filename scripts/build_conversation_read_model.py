from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.conversation_read_model import build_conversation_read_model


async def _main() -> None:
    settings = Settings()
    result = await build_conversation_read_model(settings.database_path)

    print("=" * 72)
    print(" CONVERSATION READ MODEL")
    print("=" * 72)
    print("Threads                  :", result.threads)
    print("Turns                    :", result.turns)
    print("Mapped human messages    :", result.mapped_messages)
    print("Dialogue threads         :", result.dialogue_threads)
    print("Client-only threads      :", result.client_only_threads)
    print("Manager-only threads     :", result.manager_only_threads)
    print("Client-tail threads      :", result.client_tail_threads)
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(_main())
