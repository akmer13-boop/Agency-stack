import logging
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.config import Settings
from app.observability import correlation_id_var
from app.services.agent_runner import AgentExecutionError, execute_agent
from app.telegram.access import is_telegram_user_allowed
from app.telegram.messages import build_main_menu, resolve_user_message, split_telegram_text
from app.telegram.rate_limit import UserRateLimiter

logger = logging.getLogger(__name__)
router = Router(name="agency-stack-telegram")


def _user_id(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


async def _deny_access(message: Message) -> None:
    user_id = _user_id(message)
    await message.answer(
        "Доступ к Agency Stack не предоставлен.\n"
        f"Ваш Telegram ID: {user_id if user_id is not None else 'не определён'}"
    )


@router.message(CommandStart())
async def start_handler(message: Message, settings: Settings) -> None:
    if not is_telegram_user_allowed(_user_id(message), settings):
        await _deny_access(message)
        return

    await message.answer(
        "Agency Stack запущен. Напишите задачу обычным текстом или выберите пункт меню.",
        reply_markup=build_main_menu(),
    )


@router.message(Command("id"))
async def id_handler(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {_user_id(message)}")


@router.message(F.text)
async def text_handler(
    message: Message,
    settings: Settings,
    rate_limiter: UserRateLimiter,
) -> None:
    user_id = _user_id(message)
    if not is_telegram_user_allowed(user_id, settings):
        await _deny_access(message)
        return

    if user_id is None or message.text is None:
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

    correlation_id = f"tg-{uuid.uuid4()}"
    token = correlation_id_var.set(correlation_id)
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            result = await execute_agent(resolve_user_message(text), settings)
    except AgentExecutionError as exc:
        logger.warning(
            "Telegram agent request failed",
            extra={"event": "telegram_agent_error", "user_id": user_id},
        )
        await message.answer(f"Сервис временно недоступен: {exc.public_message}")
        return
    finally:
        correlation_id_var.reset(token)

    for chunk in split_telegram_text(result.answer, settings.telegram_reply_chunk_size):
        await message.answer(chunk)
