import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.config import Settings
from app.domain import UserRole
from app.integrations.bitrix24 import (
    Bitrix24ConfigurationError,
    Bitrix24RequestError,
)
from app.services.bitrix24_sync import (
    format_sync_result,
    format_sync_status,
    get_bitrix_sync_status,
    run_initial_bitrix_sync,
)
from app.storage.conversation_store import ConversationStore
from app.telegram.access import get_telegram_user_role, is_telegram_user_allowed
from app.telegram.messages import split_telegram_text

router = Router(name="bitrix24-sync")
SYNC_RUN_ROLES = frozenset({UserRole.ADMIN, UserRole.MANAGER})
SYNC_STATUS_ROLES = frozenset({UserRole.ADMIN, UserRole.MANAGER, UserRole.OBSERVER})
_sync_lock = asyncio.Lock()


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


async def _sync_user(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> UserRole | None:
    user_id = _user_id(message)
    if user_id is None or message.from_user is None:
        return None
    role = get_telegram_user_role(user_id, settings)
    await conversation_store.upsert_user(
        user_id,
        username=message.from_user.username,
        display_name=message.from_user.full_name,
        role=role,
    )
    return role


async def _send_long_text(message: Message, text: str, settings: Settings) -> None:
    for chunk in split_telegram_text(text, settings.telegram_reply_chunk_size):
        await message.answer(chunk)


@router.message(Command("bitrix_sync"))
async def bitrix_sync_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await message.answer("Доступ к Agency Stack не предоставлен.")
        return

    role = await _sync_user(message, settings, conversation_store)
    if role not in SYNC_RUN_ROLES:
        await message.answer("Запуск синхронизации разрешён только руководителю или администратору.")
        return

    if _sync_lock.locked():
        await message.answer("Синхронизация Bitrix24 уже выполняется. Используйте /bitrix_sync_status.")
        return

    await message.answer(
        "Запускаю локальную read-only синхронизацию Bitrix24. "
        "Данные будут сохранены только в SQLite; запись в CRM не выполняется."
    )

    try:
        async with _sync_lock:
            async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
                result = await run_initial_bitrix_sync(settings)
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        await message.answer(f"Синхронизация Bitrix24 остановлена: {exc}")
        return
    except Exception:
        await message.answer(
            "Синхронизация Bitrix24 остановлена из-за внутренней ошибки. "
            "Подробности сохранены только в локальном состоянии запуска."
        )
        return

    await _send_long_text(message, format_sync_result(result), settings)


@router.message(Command("bitrix_sync_status"))
async def bitrix_sync_status_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await message.answer("Доступ к Agency Stack не предоставлен.")
        return

    role = await _sync_user(message, settings, conversation_store)
    if role not in SYNC_STATUS_ROLES:
        await message.answer("Статус синхронизации недоступен для вашей роли.")
        return

    status, counts = await get_bitrix_sync_status(settings)
    await _send_long_text(message, format_sync_status(status, counts), settings)
