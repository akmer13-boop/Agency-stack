from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.config import Settings
from app.services.rop_leads import build_and_format_lead_intelligence
from app.storage.conversation_store import ConversationStore
from app.telegram.rop_handlers import _authorize, _send_long_text

router = Router(name="rop-lead-intelligence")


@router.message(Command("rop_leads"))
async def rop_leads_handler(
    message: Message,
    settings: Settings,
    conversation_store: ConversationStore,
) -> None:
    if not await _authorize(message, settings, conversation_store):
        return

    parts = (message.text or "").strip().split()
    if len(parts) > 2:
        await message.answer("Использование: /rop_leads 7")
        return

    days = 7
    if len(parts) == 2:
        if not parts[1].isdigit():
            await message.answer("Период должен быть числом дней: от 1 до 365.")
            return
        days = int(parts[1])
    if days < 1 or days > 365:
        await message.answer("Период должен быть от 1 до 365 дней.")
        return

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        text = await build_and_format_lead_intelligence(settings, days)
    await _send_long_text(message, text, settings)
