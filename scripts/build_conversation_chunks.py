from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.conversation_chunks import (
    MAX_CHUNK_TEXT_CHARS,
    build_conversation_chunks,
)


async def _main() -> None:
    settings = Settings()
    result = await build_conversation_chunks(settings.database_path)

    print("=" * 78)
    print(" CONVERSATION SEMANTIC CHUNKS")
    print("=" * 78)
    print("Max text chars/chunk     :", MAX_CHUNK_TEXT_CHARS)
    print("Episodes                 :", result.episodes)
    print("Chunks                   :", result.chunks)
    print("Text chunks              :", result.text_chunks)
    print("Zero-text chunks         :", result.zero_text_chunks)
    print("Split episodes           :", result.split_episodes)
    print("Max chunks/episode       :", result.max_chunks_per_episode)
    print("Raw human messages       :", result.raw_messages)
    print("Distinct mapped messages :", result.distinct_mapped_messages)
    print("Segments                 :", result.segments)
    print("Split raw messages       :", result.split_messages)
    print("Total text chars         :", result.total_text_chars)
    print("Max actual chunk chars   :", result.max_chunk_text_chars)
    print(
        "Duplicate content hashes:",
        result.duplicate_content_fingerprints,
    )
    print("Reusable duplicate chunks:", result.reusable_duplicate_chunks)
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(_main())
