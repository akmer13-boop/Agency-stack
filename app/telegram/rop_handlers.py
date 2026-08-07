from __future__ import annotations

from collections.abc import Callable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.config import Settings
from app.domain import UserRole
from app.services.rop_analytics import (
    RopSnapshot,
    build_rop_snapshot,
    format_rop_funnel,
    format_rop_risks,
    format_rop_today,
)
from app.storage.conversation_store import ConversationStore
from app.telegram.access import get_telegram_user_role, is_telegram_user_allowed
from app.telegram.messages import split_telegram_text

router = Router(name="rop-analytics")
ROP_READ_ROLES = frozenset({UserRole.ADMIN, UserRole.MANAGER, UserRole.OBSERVER})


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


async def _authorize(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> bool:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await message.answer("Доступ к Agency Stack не предоставлен.")
        return False
    if user_id is None or message.from_user is None:
        return False

    role = get_telegram_user_role(user_id, settings)
    await conversation_store.upsert_user(
        user_id,
        username=message.from_user.username,
        display_name=message.from_user.full_name,
        role=role,
    )
    if role not in ROP_READ_ROLES:
        await message.answer("Аналитика ИИ-РОПа недоступна для вашей роли.")
        return False
    return True


async def _send_long_text(message: Message, text: str, settings: Settings) -> None:
    for chunk in split_telegram_text(text, settings.telegram_reply_chunk_size):
        await message.answer(chunk)


async def _render_snapshot(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
    formatter: Callable[[RopSnapshot], str],
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        snapshot = await build_rop_snapshot(
            settings.database_path,
            attention_days=settings.rop_attention_days,
            critical_days=settings.rop_critical_days,
            risk_limit=settings.rop_risk_limit,
        )
    await _send_long_text(message, formatter(snapshot), settings)


@router.message(Command("rop_today"))
async def rop_today_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(
        message,
        settings,
        conversation_store,
        format_rop_today,
    )


@router.message(Command("rop_funnel"))
async def rop_funnel_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(
        message,
        settings,
        conversation_store,
        format_rop_funnel,
    )


@router.message(Command("rop_risks"))
async def rop_risks_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(
        message,
        settings,
        conversation_store,
        format_rop_risks,
    )
