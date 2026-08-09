import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from app.config import get_settings
from app.observability import configure_logging
from app.proxy import build_proxy_url
from app.runtime import configure_openai_runtime
from app.storage.conversation_store import ConversationStore
from app.telegram.bitrix_inventory_handlers import router as bitrix_inventory_router
from app.telegram.bitrix_sync_handlers import router as bitrix_sync_router
from app.telegram.handlers import router
from app.telegram.rate_limit import UserRateLimiter
from app.telegram.rop_handlers import router as rop_router
from app.telegram.rop_lead_handlers import router as rop_lead_router

logger = logging.getLogger(__name__)


async def run_telegram_bot() -> None:
    configure_logging()
    settings = get_settings()

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    if not settings.allowed_telegram_user_ids:
        logger.warning(
            "Telegram allowlist is empty; all users will be denied",
            extra={"event": "telegram_allowlist_empty"},
        )

    configure_openai_runtime(settings)

    conversation_store = ConversationStore(
        settings.database_path,
        settings.conversation_history_limit,
    )
    await conversation_store.initialize()

    proxy_url = build_proxy_url(settings, remote_dns=False)
    if proxy_url:
        session = AiohttpSession(proxy=proxy_url)
        bot = Bot(token=settings.telegram_bot_token, session=session)
        logger.info(
            "Outbound proxy enabled for Telegram",
            extra={
                "event": "telegram_proxy_enabled",
                "proxy_type": settings.proxy_type.strip().lower(),
                "proxy_auth": settings.proxy_uses_credentials,
            },
        )
    else:
        bot = Bot(token=settings.telegram_bot_token)

    dispatcher = Dispatcher()
    dispatcher.include_router(bitrix_inventory_router)
    dispatcher.include_router(bitrix_sync_router)
    dispatcher.include_router(rop_router)
    dispatcher.include_router(rop_lead_router)
    dispatcher.include_router(router)
    rate_limiter = UserRateLimiter(settings.telegram_request_cooldown_seconds)

    await bot.delete_webhook(drop_pending_updates=False)
    logger.info(
        "Starting Telegram polling",
        extra={
            "event": "telegram_polling_start",
            "database_path": settings.database_path,
        },
    )
    await dispatcher.start_polling(
        bot,
        settings=settings,
        rate_limiter=rate_limiter,
        conversation_store=conversation_store,
        polling_timeout=settings.telegram_polling_timeout_seconds,
        tasks_concurrency_limit=8,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )


def main() -> None:
    asyncio.run(run_telegram_bot())


if __name__ == "__main__":
    main()
