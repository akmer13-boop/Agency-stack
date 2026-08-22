from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

MAIN_MENU_PROMPTS = {
    "🤖 Рекомендации ИИ-РОПа": (
        "Что требует внимания руководителя отдела продаж сегодня именно в B2C? "
        "Обязательно используй get_rop_b2c_today_focus и реальные локальные данные CRM. "
        "Назови не более "
        "трёх подтверждённых сигналов и трёх управленческих рекомендаций. "
        "Не показывай B2B, технические коды стадий, суммы и валюты. "
        "Сохрани кликабельные номера карточек. Отделяй факты от гипотез."
    ),
    "🔥 Сделки, требующие внимания": (
        "Какие конкретные B2C-сделки требуют внимания сегодня? "
        "Обязательно используй get_rop_b2c_today_focus и назови не более "
        "пяти приоритетных сделок с подтверждённой причиной внимания. "
        "Сохрани кликабельные номера карточек. Не показывай B2B, технические "
        "коды стадий, суммы, валюты и legacy-ярлык КРИТИЧНО. Не придумывай причины."
    ),
}


def build_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 ИИ-РОП")],
            [KeyboardButton(text="🤖 Рекомендации ИИ-РОПа")],
            [KeyboardButton(text="🔥 Сделки, требующие внимания")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Задайте вопрос ИИ-РОПу",
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
