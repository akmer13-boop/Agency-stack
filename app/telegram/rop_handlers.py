from __future__ import annotations

from collections.abc import Callable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.config import Settings
from app.domain import UserRole
from app.integrations.bitrix24 import Bitrix24ConfigurationError, Bitrix24RequestError
from app.services.rop_activity_risk import (
    build_activity_aware_risk,
    format_activity_aware_risk,
)
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
from app.services.rop_daily import build_rop_daily
from app.services.rop_deal import build_deal_drilldown, format_deal_drilldown
from app.services.rop_deal_evidence import (
    build_deal_stage_evidence,
    format_deal_stage_evidence,
)
from app.services.rop_deal_vitality import (
    build_deal_vitality,
    format_deal_vitality,
)
from app.services.rop_deep_analytics import (
    build_loss_report,
    build_manager_report,
    build_stage_aging_report,
    format_loss_report,
    format_manager_report,
    format_stage_aging_report,
)
from app.services.rop_directory import (
    enrich_responsible_ids,
    format_directory_sync_result,
    load_rop_directory,
    sync_rop_directory,
)
from app.services.rop_mvp3 import (
    build_cycle_time_report,
    build_focus_report,
    build_stage_sla_report,
    format_cycle_time_report,
    format_focus_report,
    format_stage_sla_report,
)
from app.services.rop_recent_activity import (
    build_recent_deal_activity,
    format_recent_deal_activity,
)
from app.services.rop_scheduler import (
    build_rop_scheduler_plan,
    format_rop_scheduler_plan,
)
from app.services.rop_scheduler_health import (
    build_rop_scheduler_health,
    format_rop_scheduler_health,
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


async def _with_local_identities(text: str, settings: Settings) -> str:
    directory = await load_rop_directory(settings.database_path)
    return enrich_responsible_ids(text, directory)


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


@router.message(Command("bitrix_directory_sync"))
async def bitrix_directory_sync_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await sync_rop_directory(settings)
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        await message.answer(f"Справочник Bitrix24 не обновлён: {exc}")
        return
    await _send_long_text(message, format_directory_sync_result(result), settings)


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
        text = await _with_local_identities(format_loss_report(report), settings)
    await _send_long_text(message, text, settings)


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
        text = format_manager_report(
            report,
            min_closed_sample=settings.rop_manager_min_closed_sample,
        )
        text = await _with_local_identities(text, settings)
    await _send_long_text(message, text, settings)


@router.message(Command("rop_sla"))
async def rop_sla_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_stage_sla_report(
            settings.database_path,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
    await _send_long_text(message, format_stage_sla_report(report), settings)


@router.message(Command("rop_cycle_time"))
async def rop_cycle_time_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_cycle_time_report(
            settings.database_path,
            timezone_name=settings.rop_timezone,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
    await _send_long_text(message, format_cycle_time_report(report), settings)


@router.message(Command("rop_focus"))
async def rop_focus_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_focus_report(
            settings.database_path,
            limit=settings.rop_focus_limit,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        text = await _with_local_identities(format_focus_report(report), settings)
    await _send_long_text(message, text, settings)


@router.message(Command("rop_daily"))
async def rop_daily_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        text = await build_rop_daily(settings)
    await _send_long_text(message, text, settings)


@router.message(Command("rop_scheduler_status"))
async def rop_scheduler_status_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    plan = build_rop_scheduler_plan(settings)
    await _send_long_text(message, format_rop_scheduler_plan(plan), settings)


@router.message(Command("rop_scheduler_health"))
async def rop_scheduler_health_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return
    report = build_rop_scheduler_health(settings)
    await _send_long_text(message, format_rop_scheduler_health(report), settings)


@router.message(Command("rop_deal"))
async def rop_deal_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return

    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Использование: /rop_deal 7040")
        return

    deal_id = parts[1].strip()
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_deal_drilldown(
            settings,
            deal_id,
            include_timeline_comments=True,
        )
        evidence = await build_deal_stage_evidence(settings, report) if report is not None else None
        risk = (
            build_activity_aware_risk(report, evidence)
            if report is not None and evidence is not None
            else None
        )
        vitality = (
            build_deal_vitality(report, risk) if report is not None and risk is not None else None
        )
    if report is None or evidence is None or risk is None or vitality is None:
        await message.answer(f"Сделка #{deal_id} не найдена в локальной синхронизированной CRM.")
        return

    base_text = format_deal_drilldown(
        report,
        timezone_name=settings.rop_timezone,
    )
    evidence_text = format_deal_stage_evidence(
        report,
        evidence,
        timezone_name=settings.rop_timezone,
    )
    risk_text = format_activity_aware_risk(risk)
    vitality_text = format_deal_vitality(vitality)
    await _send_long_text(
        message,
        f"{base_text}\n\n{evidence_text}\n\n{risk_text}\n\n{vitality_text}",
        settings,
    )


@router.message(Command("rop_deal_activity"))
async def rop_deal_activity_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return

    parts = (message.text or "").strip().split()
    if len(parts) not in {2, 3} or not parts[1].isdigit():
        await message.answer("Использование: /rop_deal_activity 7040 7")
        return

    deal_id = parts[1]
    days = 7
    if len(parts) == 3:
        if not parts[2].isdigit():
            await message.answer("Период должен быть числом дней: от 1 до 365.")
            return
        days = int(parts[2])
    if days < 1 or days > 365:
        await message.answer("Период должен быть от 1 до 365 дней.")
        return

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        report = await build_deal_drilldown(
            settings,
            deal_id,
            include_timeline_comments=False,
        )
        activity = (
            await build_recent_deal_activity(settings, report, days) if report is not None else None
        )
    if report is None or activity is None:
        await message.answer(f"Сделка #{deal_id} не найдена в локальной синхронизированной CRM.")
        return

    text = format_recent_deal_activity(
        report,
        activity,
        timezone_name=settings.rop_timezone,
    )
    await _send_long_text(message, text, settings)
