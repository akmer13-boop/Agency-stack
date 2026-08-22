from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.storage.conversation_store import ConversationStore
from app.telegram import rop_mvp_dashboard_handlers as dashboard_handlers
from app.telegram.messages import build_main_menu
from app.telegram.rop_mvp_dashboard_handlers import (
    CALLBACK_PREFIX,
    ROP_MENU_BUTTON,
    build_rop_mvp_keyboard,
    rop_mvp_dashboard_callback,
    rop_mvp_dashboard_handler,
    select_dashboard_section,
)

FULL_DASHBOARD = """ИИ-РОП · B2C Dashboard
Срез данных: 22.08.2026 15:43 МСК

B2C · текущий месяц
• активные B2C-сделки: 1052

First Response SLA · 15 бизнес-минут
• в срок: 218
• нарушение: 331

Stage SLA · контролируемые B2C-сделки
• в контроле: 611
• требуют внимания: 224

Менеджеры · приоритет разбора
• Ольга Попкова: требуют внимания 76

Самые просроченные сделки по Stage SLA
• #7444 · Подбор пакетного тура

Статусы Stage SLA: таймер не достигнут / требует внимания.
Система анализирует CRM в read-only режиме и не изменяет Bitrix24."""

SUMMARY_DASHBOARD = """ИИ-РОП · B2C
Срез данных: 22.08.2026 15:43 МСК

Поток месяца
• лиды: 846 · новые сделки: 376

Первый ответ · 15 бизнес-минут
• соблюдение: 39.7% (n=549)
• нарушений: 331 · недостаточно данных: 285

Активные сделки
• всего: 1052
• требуют внимания: 224 · недостаточно данных: 23
• SLA пока не настроен: 309"""


def make_settings(**overrides) -> Settings:
    defaults = {
        "_env_file": None,
        "openai_api_key": "test-key",
        "telegram_manager_user_ids": "42",
        "telegram_request_cooldown_seconds": 0,
        "database_path": "unused.db",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_message(*, user_id: int, text: str = "/rop"):
    return SimpleNamespace(
        from_user=SimpleNamespace(
            id=user_id,
            username=f"user{user_id}",
            full_name=f"User {user_id}",
        ),
        text=text,
        answer=AsyncMock(),
        bot=object(),
        chat=SimpleNamespace(id=100),
    )


@pytest.fixture
async def conversation_store(tmp_path: Path) -> ConversationStore:
    store = ConversationStore(str(tmp_path / "telegram.db"), history_limit=12)
    await store.initialize()
    return store


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_m3_main_menu_opens_real_rop_dashboard() -> None:
    menu = build_main_menu()
    assert menu.keyboard[0][0].text == ROP_MENU_BUTTON


def test_m3_inline_keyboard_has_all_product_sections() -> None:
    keyboard = build_rop_mvp_keyboard()
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert callbacks == {
        f"{CALLBACK_PREFIX}summary",
        f"{CALLBACK_PREFIX}full",
        f"{CALLBACK_PREFIX}first",
        f"{CALLBACK_PREFIX}stage",
        f"{CALLBACK_PREFIX}managers",
        f"{CALLBACK_PREFIX}deals",
        f"{CALLBACK_PREFIX}refresh",
    }


@pytest.mark.parametrize(
    ("section", "included", "excluded"),
    (
        ("first_response", "• в срок: 218", "• в контроле: 611"),
        ("stage_sla", "• в контроле: 611", "Ольга Попкова"),
        ("managers", "Ольга Попкова", "#7444"),
        ("deals", "#7444", "Ольга Попкова"),
    ),
)
def test_m3_dashboard_sections_are_isolated(
    section: str,
    included: str,
    excluded: str,
) -> None:
    selected = select_dashboard_section(FULL_DASHBOARD, section)
    assert selected.startswith("ИИ-РОП · B2C Dashboard")
    assert included in selected
    assert excluded not in selected
    assert "Bitrix24 не изменяется" in selected


@pytest.mark.asyncio
async def test_m3_dashboard_cache_serves_buttons_and_refresh_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    builds: list[object] = []

    def fake_dashboard(_settings: Settings) -> object:
        dashboard = object()
        builds.append(dashboard)
        return dashboard

    monkeypatch.setattr(
        dashboard_handlers,
        "_dashboard_cache",
        None,
    )
    monkeypatch.setattr(
        dashboard_handlers,
        "build_b2c_mvp_dashboard",
        fake_dashboard,
    )
    monkeypatch.setattr(
        dashboard_handlers,
        "format_b2c_mvp_dashboard",
        lambda _dashboard: FULL_DASHBOARD,
    )
    monkeypatch.setattr(
        dashboard_handlers,
        "format_b2c_mvp_summary",
        lambda _dashboard: SUMMARY_DASHBOARD,
    )

    summary = await dashboard_handlers._build_dashboard_text(
        settings,
        "summary",
    )
    first = await dashboard_handlers._build_dashboard_text(
        settings,
        "first_response",
    )
    refreshed = await dashboard_handlers._build_dashboard_text(
        settings,
        "refresh",
    )

    assert summary == SUMMARY_DASHBOARD
    assert "• в срок: 218" in first
    assert refreshed == SUMMARY_DASHBOARD
    assert len(builds) == 2


@pytest.mark.asyncio
async def test_m3_rop_command_sends_dashboard_with_buttons(
    monkeypatch: pytest.MonkeyPatch,
    conversation_store: ConversationStore,
) -> None:
    message = make_message(user_id=42)
    settings = make_settings()

    async def fake_build(_settings: Settings, section: str) -> str:
        assert section == "summary"
        return SUMMARY_DASHBOARD

    monkeypatch.setattr(
        "app.telegram.rop_mvp_dashboard_handlers._build_dashboard_text",
        fake_build,
    )
    monkeypatch.setattr(
        "app.telegram.rop_mvp_dashboard_handlers.ChatActionSender.typing",
        lambda **_kwargs: FakeTyping(),
    )

    await rop_mvp_dashboard_handler(
        message,
        settings,
        conversation_store,
    )

    assert message.answer.await_count == 1
    assert "ИИ-РОП · B2C" in message.answer.await_args.args[0]
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == f"{CALLBACK_PREFIX}summary"
    assert keyboard.inline_keyboard[0][1].callback_data == f"{CALLBACK_PREFIX}full"


@pytest.mark.asyncio
async def test_m3_callback_is_role_protected(
    conversation_store: ConversationStore,
) -> None:
    message = make_message(user_id=42)
    callback = SimpleNamespace(
        from_user=message.from_user,
        data=f"{CALLBACK_PREFIX}first",
        message=message,
        answer=AsyncMock(),
    )
    settings = make_settings(
        telegram_manager_user_ids="",
        telegram_allowed_user_ids="42",
    )

    await rop_mvp_dashboard_callback(
        callback,
        settings,
        conversation_store,
    )

    assert callback.answer.await_count == 1
    assert "недоступна" in callback.answer.await_args.args[0]
    assert callback.answer.await_args.kwargs["show_alert"] is True
    message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_m3_callback_renders_selected_section(
    monkeypatch: pytest.MonkeyPatch,
    conversation_store: ConversationStore,
) -> None:
    message = make_message(user_id=42)
    callback = SimpleNamespace(
        from_user=message.from_user,
        data=f"{CALLBACK_PREFIX}first",
        message=message,
        answer=AsyncMock(),
    )
    settings = make_settings()
    captured: list[str] = []

    async def fake_build(_settings: Settings, section: str) -> str:
        captured.append(section)
        return select_dashboard_section(FULL_DASHBOARD, section)

    monkeypatch.setattr(
        "app.telegram.rop_mvp_dashboard_handlers._build_dashboard_text",
        fake_build,
    )
    monkeypatch.setattr(
        "app.telegram.rop_mvp_dashboard_handlers.ChatActionSender.typing",
        lambda **_kwargs: FakeTyping(),
    )

    await rop_mvp_dashboard_callback(
        callback,
        settings,
        conversation_store,
    )

    assert captured == ["first_response"]
    assert callback.answer.await_args.args[0] == "Обновляю данные…"
    assert "• в срок: 218" in message.answer.await_args.args[0]
    assert "• в контроле: 611" not in message.answer.await_args.args[0]
