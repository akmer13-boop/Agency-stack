from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from app.config import Settings
from app.services.rop_b2c_mvp_dashboard import (
    build_b2c_mvp_dashboard,
    format_b2c_mvp_dashboard,
    format_b2c_mvp_summary,
)
from app.storage.conversation_store import ConversationStore
from app.telegram.access import get_telegram_user_role, is_telegram_user_allowed
from app.telegram.messages import split_telegram_text
from app.telegram.rich_text import render_safe_crm_links_html
from app.telegram.rop_handlers import ROP_READ_ROLES, _authorize

logger = logging.getLogger(__name__)
router = Router(name="rop-b2c-mvp-dashboard")

ROP_MENU_BUTTON = "📊 ИИ-РОП"
CALLBACK_PREFIX = "rop_b2c:"
DASHBOARD_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class DashboardCacheEntry:
    database_path: str
    stored_at: float
    summary_text: str
    full_text: str


_dashboard_cache: DashboardCacheEntry | None = None

CALLBACK_TO_SECTION: Mapping[str, str] = {
    f"{CALLBACK_PREFIX}summary": "summary",
    f"{CALLBACK_PREFIX}full": "full",
    f"{CALLBACK_PREFIX}first": "first_response",
    f"{CALLBACK_PREFIX}stage": "stage_sla",
    f"{CALLBACK_PREFIX}managers": "managers",
    f"{CALLBACK_PREFIX}deals": "deals",
    f"{CALLBACK_PREFIX}refresh": "refresh",
}

SECTION_BOUNDS: Mapping[str, tuple[str, str]] = {
    "first_response": (
        "First Response SLA · 15 бизнес-минут",
        "Stage SLA · контролируемые B2C-сделки",
    ),
    "stage_sla": (
        "Stage SLA · контролируемые B2C-сделки",
        "Менеджеры · приоритет разбора",
    ),
    "managers": (
        "Менеджеры · приоритет разбора",
        "Самые просроченные сделки по Stage SLA",
    ),
    "deals": (
        "Самые просроченные сделки по Stage SLA",
        "Система анализирует CRM в read-only режиме",
    ),
}


def build_rop_mvp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Сводка",
                    callback_data=f"{CALLBACK_PREFIX}summary",
                ),
                InlineKeyboardButton(
                    text="📄 Полный отчёт",
                    callback_data=f"{CALLBACK_PREFIX}full",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱ Первый ответ",
                    callback_data=f"{CALLBACK_PREFIX}first",
                ),
                InlineKeyboardButton(
                    text="🚦 Stage SLA",
                    callback_data=f"{CALLBACK_PREFIX}stage",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Менеджеры",
                    callback_data=f"{CALLBACK_PREFIX}managers",
                ),
                InlineKeyboardButton(
                    text="🔥 Просроченные",
                    callback_data=f"{CALLBACK_PREFIX}deals",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data=f"{CALLBACK_PREFIX}refresh",
                )
            ],
        ]
    )


def _line_index(lines: list[str], prefix: str) -> int:
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index
    raise ValueError(f"dashboard_section_marker_missing:{prefix}")


def select_dashboard_section(full_text: str, section: str) -> str:
    if section == "full":
        return full_text

    bounds = SECTION_BOUNDS.get(section)
    if bounds is None:
        raise ValueError(f"unknown_dashboard_section:{section}")

    lines = full_text.splitlines()
    if len(lines) < 2:
        raise ValueError("dashboard_header_missing")

    start = _line_index(lines, bounds[0])
    end = _line_index(lines, bounds[1])
    if end <= start:
        raise ValueError(f"dashboard_section_order_invalid:{section}")

    section_lines = lines[start:end]
    while section_lines and not section_lines[-1]:
        section_lines.pop()

    result = [
        lines[0],
        lines[1],
        "",
        *section_lines,
        "",
        "Источник: локальная CRM-копия · Bitrix24 не изменяется.",
    ]
    return "\n".join(result)


async def _build_dashboard_text(
    settings: Settings,
    section: str,
) -> str:
    global _dashboard_cache

    force_refresh = section == "refresh"
    selected_section = (
        "summary"
        if force_refresh
        else section
    )
    now = monotonic()
    cached = _dashboard_cache

    if (
        not force_refresh
        and cached is not None
        and cached.database_path
        == settings.database_path
        and now - cached.stored_at
        <= DASHBOARD_CACHE_TTL_SECONDS
    ):
        if selected_section == "summary":
            return cached.summary_text
        return select_dashboard_section(
            cached.full_text,
            selected_section,
        )

    dashboard = await asyncio.to_thread(
        build_b2c_mvp_dashboard,
        settings,
    )
    summary_text = format_b2c_mvp_summary(dashboard)
    full_text = format_b2c_mvp_dashboard(dashboard)
    _dashboard_cache = DashboardCacheEntry(
        database_path=settings.database_path,
        stored_at=monotonic(),
        summary_text=summary_text,
        full_text=full_text,
    )
    if selected_section == "summary":
        return summary_text
    return select_dashboard_section(
        full_text,
        selected_section,
    )


async def _send_dashboard_text(
    message: Message,
    text: str,
    settings: Settings,
) -> None:
    chunks = split_telegram_text(
        text,
        settings.telegram_reply_chunk_size,
    )
    keyboard = build_rop_mvp_keyboard()

    for index, chunk in enumerate(chunks):
        reply_markup = (
            keyboard
            if index == len(chunks) - 1
            else None
        )
        rich_chunk = render_safe_crm_links_html(
            chunk,
            settings,
        )
        if rich_chunk is None:
            await message.answer(
                chunk,
                reply_markup=reply_markup,
            )
        else:
            await message.answer(
                rich_chunk,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )


async def _render_dashboard(
    message: Message,
    settings: Settings,
    section: str,
) -> None:
    try:
        async with ChatActionSender.typing(
            bot=message.bot,
            chat_id=message.chat.id,
        ):
            text = await _build_dashboard_text(
                settings,
                section,
            )
    except Exception as exc:
        logger.exception(
            "B2C MVP dashboard render failed",
            extra={
                "event": "rop_b2c_mvp_dashboard_error",
                "error_type": type(exc).__name__,
            },
        )
        await message.answer(
            "Не удалось построить B2C Dashboard по локальной базе. "
            "Попробуйте ещё раз после следующей синхронизации."
        )
        return

    await _send_dashboard_text(
        message,
        text,
        settings,
    )


async def _authorize_callback(
    callback: CallbackQuery,
    settings: Settings,
    conversation_store: ConversationStore,
) -> bool:
    user = callback.from_user
    user_id = user.id

    if not is_telegram_user_allowed(
        user_id,
        settings,
    ):
        await callback.answer(
            "Доступ к Agency Stack не предоставлен.",
            show_alert=True,
        )
        return False

    role = get_telegram_user_role(
        user_id,
        settings,
    )
    await conversation_store.upsert_user(
        user_id,
        username=user.username,
        display_name=user.full_name,
        role=role,
    )

    if role not in ROP_READ_ROLES:
        await callback.answer(
            "Аналитика ИИ-РОПа недоступна для вашей роли.",
            show_alert=True,
        )
        return False

    return True


@router.message(Command("rop"))
@router.message(F.text == ROP_MENU_BUTTON)
async def rop_mvp_dashboard_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(
        message,
        settings,
        conversation_store,
    ):
        return

    await _render_dashboard(
        message,
        settings,
        "summary",
    )


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def rop_mvp_dashboard_callback(
    callback: CallbackQuery,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize_callback(
        callback,
        settings,
        conversation_store,
    ):
        return

    section = CALLBACK_TO_SECTION.get(
        callback.data or ""
    )
    if section is None:
        await callback.answer(
            "Кнопка устарела. Откройте /rop заново.",
            show_alert=True,
        )
        return

    if callback.message is None:
        await callback.answer(
            "Сообщение Dashboard уже недоступно.",
            show_alert=True,
        )
        return

    await callback.answer("Обновляю данные…")
    await _render_dashboard(
        callback.message,
        settings,
        section,
    )
