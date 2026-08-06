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
from app.services.bitrix24_inventory import (
    fetch_bitrix24_inventory,
    format_bitrix24_inventory,
)
from app.storage.conversation_store import ConversationStore
from app.telegram.access import get_telegram_user_role, is_telegram_user_allowed
from app.telegram.messages import split_telegram_text


router = Router(name="bitrix24-inventory")
BITRIX_INVENTORY_ROLES = frozenset(
    {UserRole.ADMIN, UserRole.MANAGER, UserRole.OBSERVER}
)


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


@router.message(Command("bitrix_inventory"))
async def bitrix_inventory_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await message.answer(
            "Доступ к Agency Stack не предоставлен.\n"
            f"Ваш Telegram ID: {user_id if user_id is not None else 'не определён'}"
        )
        return
    if user_id is None:
        return

    role = get_telegram_user_role(user_id, settings)
    if role not in BITRIX_INVENTORY_ROLES:
        await message.answer(
            "Инвентаризация Bitrix24 разрешена только руководителю, "
            "администратору или наблюдателю."
        )
        return

    if message.from_user is not None:
        await conversation_store.upsert_user(
            user_id,
            username=message.from_user.username,
            display_name=message.from_user.full_name,
            role=role,
        )

    try:
        async with ChatActionSender.typing(
            bot=message.bot,
            chat_id=message.chat.id,
        ):
            inventory = await fetch_bitrix24_inventory(settings)
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        await message.answer(f"Инвентаризация Bitrix24 не выполнена: {exc}")
        return

    text = format_bitrix24_inventory(inventory)
    for chunk in split_telegram_text(text, settings.telegram_reply_chunk_size):
        await message.answer(chunk)
