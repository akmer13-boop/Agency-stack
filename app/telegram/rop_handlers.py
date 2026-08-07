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
    format_rop_month,
    format_rop_pipeline,
    format_rop_risks,
    format_rop_today,
    format_rop_week,
)
from app.services.rop_deep_analytics import (
    build_loss_report,
    build_manager_report,
    build_stage_aging_report,
    format_loss_report,
    format_manager_report,
    format_stage_aging_report,
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


async def _build_snapshot(settings: Settings) -> RopSnapshot:
    return await build_rop_snapshot(
        settings.database_path,
        attention_days=settings.rop_attention_days,
        critical_days=settings.rop_critical_days,
        risk_limit=settings.rop_risk_limit,
        timezone_name=settings.rop_timezone,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )


async def _render_snapshot(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
    formatter: Callable[[RopSnapshot], str],
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        snapshot = await _build_snapshot(settings)
    await _send_long_text(message, formatter(snapshot), settings)


@router.message(Command("rop_today"))
async def rop_today_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(message, settings, conversation_store, format_rop_today)


@router.message(Command("rop_week"))
async def rop_week_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(message, settings, conversation_store, format_rop_week)


@router.message(Command("rop_month"))
async def rop_month_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(message, settings, conversation_store, format_rop_month)


@router.message(Command("rop_pipeline"))
async def rop_pipeline_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(message, settings, conversation_store, format_rop_pipeline)


@router.message(Command("rop_funnel"))
async def rop_funnel_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(message, settings, conversation_store, format_rop_funnel)


@router.message(Command("rop_risks"))
async def rop_risks_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    await _render_snapshot(message, settings, conversation_store, format_rop_risks)


@router.message(Command("rop_losses"))
async def rop_losses_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_loss_report(
            settings.database_path,
            timezone_name=settings.rop_timezone,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
    await _send_long_text(message, format_loss_report(report), settings)


@router.message(Command("rop_stage_aging"))
async def rop_stage_aging_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_stage_aging_report(
            settings.database_path,
            attention_days=settings.rop_attention_days,
            critical_days=settings.rop_critical_days,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
    await _send_long_text(message, format_stage_aging_report(report), settings)


@router.message(Command("rop_managers"))
async def rop_managers_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_manager_report(
            settings.database_path,
            timezone_name=settings.rop_timezone,
            attention_days=settings.rop_attention_days,
            critical_days=settings.rop_critical_days,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
    await _send_long_text(message, format_manager_report(report), settings)
