from __future__ import annotations

import argparse
import asyncio

from app.config import Settings
from app.services.openlines_ingestion import run_openlines_ingestion
from app.storage.openlines_store import OpenLinesStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Bitrix24 Open Lines discovery/full-history sync."
    )
    parser.add_argument("--max-crm-objects", type=int, default=200)
    parser.add_argument("--max-chats", type=int, default=50)
    parser.add_argument("--max-pages-per-chat", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.08)

    phase = parser.add_mutually_exclusive_group()
    phase.add_argument(
        "--discovery-only",
        action="store_true",
        help="Discover CRM-linked Open Lines chats without loading message history.",
    )
    phase.add_argument(
        "--backfill-only",
        action="store_true",
        help="Load history for already discovered chats without CRM chat discovery.",
    )

    parser.add_argument(
        "--full",
        action="store_true",
        help="Use large safe bounds. Combine with --discovery-only for full discovery.",
    )
    return parser


async def _main() -> None:
    args = _parser().parse_args()

    if args.full:
        max_crm_objects = 100_000
        max_chats = 100_000
        max_pages_per_chat = 10_000
    else:
        max_crm_objects = args.max_crm_objects
        max_chats = args.max_chats
        max_pages_per_chat = args.max_pages_per_chat

    run_discovery = not args.backfill_only
    run_backfill = not args.discovery_only

    if args.discovery_only:
        mode = "discovery-only"
    elif args.backfill_only:
        mode = "backfill-only"
    else:
        mode = "discovery+backfill"

    settings = Settings()
    result = await run_openlines_ingestion(
        settings,
        max_crm_objects=max_crm_objects,
        max_chats=max_chats,
        max_pages_per_chat=max_pages_per_chat,
        request_delay_seconds=max(0.0, args.delay),
        run_discovery=run_discovery,
        run_backfill=run_backfill,
    )

    store = OpenLinesStore(settings.database_path)
    counts = await store.counts()

    print("=" * 72)
    print(" OPEN LINES FULL-HISTORY SYNC")
    print("=" * 72)
    print("Bitrix write             : NONE")
    print("Mode                     :", mode)
    print("CRM objects discovered   :", result.crm_objects_discovered)
    print("CRM objects processed    :", result.crm_objects_processed)
    print("CRM objects remaining    :", result.crm_objects_remaining)
    print("Discovery batch requests :", result.discovery_batch_requests)
    print("Chats discovered         :", result.chats_discovered)
    print("Chats processed          :", result.chats_processed)
    print("Dialog pages loaded      :", result.dialog_pages_loaded)
    print("Messages observed        :", result.messages_observed)
    print("Text observed            :", result.text_messages_observed)
    print("Manager observed         :", result.manager_messages_observed)
    print("Client observed          :", result.client_messages_observed)
    print("System observed          :", result.system_messages_observed)
    print("Bot observed             :", result.bot_messages_observed)
    print("Unknown observed         :", result.unknown_messages_observed)
    print("Files observed           :", result.files_observed)
    print("Connectors               :", result.connectors)
    print("Errors                   :", result.errors)
    print()
    print("LOCAL STORE TOTALS")
    print("Chats                    :", counts.chats)
    print("CRM links                :", counts.crm_links)
    print("Sessions                 :", counts.sessions)
    print("Messages                 :", counts.messages)
    print("Manager messages         :", counts.manager_messages)
    print("Client messages          :", counts.client_messages)
    print("System messages          :", counts.system_messages)
    print("Bot messages             :", counts.bot_messages)
    print("Unknown messages         :", counts.unknown_messages)
    print("Backfill complete chats  :", counts.backfill_complete_chats)
    print("Backfill pending chats   :", counts.backfill_pending_chats)
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(_main())
