from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.conversation_episodes import (
    EPISODE_GAP_SECONDS,
    build_conversation_episodes,
)


async def _main() -> None:
    settings = Settings()
    result = await build_conversation_episodes(settings.database_path)

    print("=" * 76)
    print(" CONVERSATION EPISODES")
    print("=" * 76)
    print("Gap threshold seconds    :", EPISODE_GAP_SECONDS)
    print("Gap threshold hours      :", EPISODE_GAP_SECONDS // 3600)
    print("Episodes                 :", result.episodes)
    print("Split boundaries         :", result.split_boundaries)
    print("Multi-episode chats      :", result.multi_episode_chats)
    print("Mapped human turns       :", result.mapped_turns)
    print("Mapped human messages    :", result.mapped_messages)
    print("Zero-text episodes       :", result.zero_text_episodes)
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(_main())
