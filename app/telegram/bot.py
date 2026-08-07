import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.config import get_settings
from app.observability import configure_logging
from app.runtime import configure_openai_runtime
from app.storage.conversation_store import ConversationStore
from app.telegram.bitrix_inventory_handlers import router as bitrix_inventory_router
from app.telegram.handlers import router
from app.telegram.rate_limit import UserRateLimiter

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

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(bitrix_inventory_router)
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
