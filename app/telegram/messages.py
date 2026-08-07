from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

MAIN_MENU_PROMPTS = {
    "📊 Отчёт РОПа": "Подготовь краткий отчёт РОПа в демонстрационном режиме.",
    "⚠️ Проблемные сделки": "Объясни, какие данные нужны для поиска проблемных сделок.",
    "📚 База знаний": "Объясни, как будет работать корпоративная база знаний.",
}


def build_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Отчёт РОПа")],
            [KeyboardButton(text="⚠️ Проблемные сделки")],
            [KeyboardButton(text="📚 База знаний")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напишите задачу агенту",
    )


def resolve_user_message(text: str) -> str:
    return MAIN_MENU_PROMPTS.get(text, text)


def split_telegram_text(text: str, chunk_size: int) -> list[str]:
    if not text:
        return ["Агент вернул пустой ответ."]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, chunk_size + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, chunk_size + 1)
        if split_at <= 0:
            split_at = chunk_size

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return chunks
