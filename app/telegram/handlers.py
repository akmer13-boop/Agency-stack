import logging
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.config import Settings
from app.domain import UserRole
from app.integrations.bitrix24 import Bitrix24ConfigurationError, Bitrix24RequestError
from app.observability import correlation_id_var
from app.services.agent_runner import AgentExecutionError, execute_agent
from app.services.bitrix24_reporting import (
    fetch_deal_categories,
    fetch_deal_summary,
    fetch_pipeline_stages,
    fetch_recent_deals,
    format_deal_categories,
    format_deal_summary,
    format_pipeline_stages,
    format_recent_deals,
)
from app.services.bitrix24_service import check_bitrix24_connection
from app.services.routing import route_message
from app.storage.conversation_store import ConversationStore
from app.telegram.access import get_telegram_user_role, is_telegram_user_allowed
from app.telegram.messages import build_main_menu, resolve_user_message, split_telegram_text
from app.telegram.rate_limit import UserRateLimiter

logger = logging.getLogger(__name__)
router = Router(name="agency-stack-telegram")
BITRIX_READ_ROLES = frozenset({UserRole.ADMIN, UserRole.MANAGER, UserRole.OBSERVER})


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


async def _deny_access(message: Message) -> None:
    user_id = _user_id(message)
    await message.answer(
        "Доступ к Agency Stack не предоставлен.\n"
        f"Ваш Telegram ID: {user_id if user_id is not None else 'не определён'}"
    )


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


async def _require_bitrix_reader(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> UserRole | None:
    if not is_telegram_user_allowed(_user_id(message), settings):
        await _deny_access(message)
        return None

    role = await _sync_user(message, settings, conversation_store)
    if role not in BITRIX_READ_ROLES:
        await message.answer(
            "Доступ к аналитике Bitrix24 разрешён только руководителю, "
            "администратору или наблюдателю."
        )
        return None
    return role


async def _send_long_text(message: Message, text: str, settings: Settings) -> None:
    for chunk in split_telegram_text(text, settings.telegram_reply_chunk_size):
        await message.answer(chunk)


async def _report_bitrix_error(
    message: Message,
    error: Bitrix24ConfigurationError | Bitrix24RequestError,
) -> None:
    logger.warning(
        "Bitrix24 report request failed",
        extra={
            "event": "bitrix24_report_error",
            "user_id": _user_id(message),
            "error_type": type(error).__name__,
        },
    )
    await message.answer(f"Не удалось прочитать данные Bitrix24: {error}")


@router.message(CommandStart())
async def start_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not is_telegram_user_allowed(_user_id(message), settings):
        await _deny_access(message)
        return

    role = await _sync_user(message, settings, conversation_store)
    await message.answer(
        "Agency Stack запущен.\n"
        f"Ваша роль: {role.label if role else 'не определена'}.\n"
        "Напишите задачу обычным текстом или выберите пункт меню.",
        reply_markup=build_main_menu(),
    )


@router.message(Command("id"))
async def id_handler(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {_user_id(message)}")


@router.message(Command("help"))
async def help_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not is_telegram_user_allowed(_user_id(message), settings):
        await _deny_access(message)
        return

    role = await _sync_user(message, settings, conversation_store)
    await message.answer(
        "Команды Agency Stack:\n"
        "/start — главное меню\n"
        "/help — список команд\n"
        "/status — статус и роль\n"
        "/bitrix_status — проверить подключение Bitrix24\n"
        "/bitrix_pipelines — показать воронки\n"
        "/bitrix_stages — показать стадии воронок\n"
        "/bitrix_deals — показать последние тестовые сделки\n"
        "/bitrix_summary — локальная сводка по сделкам\n"
        "/reset — очистить память диалога\n"
        "/id — показать Telegram ID\n\n"
        f"Текущая роль: {role.label if role else 'не определена'}."
    )


@router.message(Command("status"))
async def status_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await _deny_access(message)
        return
    if user_id is None:
        return

    role = await _sync_user(message, settings, conversation_store)
    message_count = await conversation_store.count_messages(user_id)
    bitrix_status = "настроен" if settings.bitrix24_configured else "не настроен"
    await message.answer(
        "Agency Stack работает.\n"
        f"Версия: {settings.app_version}\n"
        f"Роль: {role.label if role else 'не определена'}\n"
        f"Сообщений в памяти: {message_count}\n"
        f"Bitrix24: {bitrix_status}\n"
        f"Запись в CRM: {'разрешена' if settings.allow_crm_write else 'запрещена'}"
    )


@router.message(Command("bitrix_status"))
async def bitrix_status_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if await _require_bitrix_reader(message, settings, conversation_store) is None:
        return

    status = await check_bitrix24_connection(settings)
    if not status.configured:
        await message.answer("Bitrix24 не настроен. Добавьте BITRIX24_WEBHOOK_URL в .env.")
        return
    if not status.connected:
        await message.answer(f"Bitrix24 недоступен: {status.error}")
        return

    admin_label = "да" if status.webhook_user_is_admin else "нет"
    await message.answer(
        "Bitrix24 подключён в режиме только чтения.\n"
        f"Портал: {status.portal_host}\n"
        f"ID пользователя вебхука: {status.webhook_user_id or 'не определён'}\n"
        f"Администратор портала: {admin_label}\n"
        "Запись в CRM: запрещена"
    )


@router.message(Command("bitrix_pipelines"))
async def bitrix_pipelines_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if await _require_bitrix_reader(message, settings, conversation_store) is None:
        return

    try:
        categories = await fetch_deal_categories(settings)
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        await _report_bitrix_error(message, exc)
        return

    await _send_long_text(message, format_deal_categories(categories), settings)


@router.message(Command("bitrix_stages"))
async def bitrix_stages_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if await _require_bitrix_reader(message, settings, conversation_store) is None:
        return

    try:
        groups = await fetch_pipeline_stages(settings)
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        await _report_bitrix_error(message, exc)
        return

    await _send_long_text(message, format_pipeline_stages(groups), settings)


@router.message(Command("bitrix_deals"))
async def bitrix_deals_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if await _require_bitrix_reader(message, settings, conversation_store) is None:
        return

    try:
        deals = await fetch_recent_deals(
            settings,
            max_items=settings.bitrix24_deal_preview_limit,
        )
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        await _report_bitrix_error(message, exc)
        return

    await _send_long_text(message, format_recent_deals(deals), settings)


@router.message(Command("bitrix_summary"))
async def bitrix_summary_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if await _require_bitrix_reader(message, settings, conversation_store) is None:
        return

    try:
        summary = await fetch_deal_summary(
            settings,
            max_items=settings.bitrix24_summary_limit,
        )
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        await _report_bitrix_error(message, exc)
        return

    await _send_long_text(message, format_deal_summary(summary), settings)


@router.message(Command("reset"))
async def reset_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await _deny_access(message)
        return
    if user_id is None:
        return

    await _sync_user(message, settings, conversation_store)
    deleted_count = await conversation_store.clear_history(user_id)
    await conversation_store.record_event(
        "conversation_reset",
        telegram_user_id=user_id,
        details=f"deleted_messages={deleted_count}",
    )
    await message.answer(f"Память диалога очищена. Удалено сообщений: {deleted_count}.")


@router.message(F.text)
async def text_handler(
    message: Message,
    settings: Settings,
    rate_limiter: UserRateLimiter,
    conversation_store: ConversationStore,
) -> None:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await _deny_access(message)
        return

    if user_id is None or message.text is None:
        return

    role = await _sync_user(message, settings, conversation_store)
    if role is None:
        return

    text = message.text.strip()
    if not text:
        await message.answer("Напишите текстовую задачу.")
        return

    if len(text) > settings.telegram_max_input_chars:
        await message.answer(
            "Сообщение слишком длинное. "
            f"Допустимо не более {settings.telegram_max_input_chars} символов."
        )
        return

    retry_after = await rate_limiter.retry_after(user_id)
    if retry_after > 0:
        await message.answer(f"Подождите {retry_after:.1f} сек. перед следующим запросом.")
        return

    resolved_message = resolve_user_message(text)
    route = route_message(resolved_message)
    history = await conversation_store.get_recent_messages(user_id)

    correlation_id = f"tg-{uuid.uuid4()}"
    token = correlation_id_var.set(correlation_id)
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await execute_agent(
                resolved_message,
                settings,
                route=route,
                role=role,
                history=history,
            )
    except AgentExecutionError as exc:
        logger.warning(
            "Telegram agent request failed",
            extra={
                "event": "telegram_agent_error",
                "user_id": user_id,
                "route": route.value,
            },
        )
        await conversation_store.record_event(
            "agent_error",
            telegram_user_id=user_id,
            details=f"route={route.value};status={exc.status_code}",
        )
        await message.answer(f"Сервис временно недоступен: {exc.public_message}")
        return
    finally:
        correlation_id_var.reset(token)

    await conversation_store.add_message(
        user_id,
        role="user",
        content=text,
    )
    await conversation_store.add_message(
        user_id,
        role="assistant",
        content=result.answer,
        agent_name=result.agent,
    )
    await conversation_store.record_event(
        "agent_response",
        telegram_user_id=user_id,
        details=f"route={result.route.value};agent={result.agent}",
    )

    logger.info(
        "Telegram request routed",
        extra={
            "event": "telegram_route",
            "user_id": user_id,
            "role": role.value,
            "route": result.route.value,
            "agent": result.agent,
        },
    )

    for chunk in split_telegram_text(result.answer, settings.telegram_reply_chunk_size):
        await message.answer(chunk)
