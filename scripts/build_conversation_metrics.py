from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.conversation_metrics import build_conversation_metrics


async def _main() -> None:
    settings = Settings()
    result = await build_conversation_metrics(settings.database_path)

    print("=" * 72)
    print(" FACTUAL CONVERSATION METRICS")
    print("=" * 72)
    print("Thread metrics           :", result.thread_metrics)
    print("Response intervals       :", result.response_intervals)
    print("First responses          :", result.first_responses)
    print("Client→manager intervals :", result.client_to_manager_intervals)
    print("Manager→client intervals :", result.manager_to_client_intervals)
    print("Client-tail threads      :", result.client_tail_threads)
    print("Manager handoffs         :", result.manager_handoffs)
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(_main())
