from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.domain import AgentRoute, UserRole
from app.services.agent_runner import AgentRunResult
from app.storage.conversation_store import ConversationStore
from app.telegram.access import get_telegram_user_role, is_telegram_user_allowed
from app.telegram.handlers import reset_handler, start_handler, text_handler
from app.telegram.messages import resolve_user_message, split_telegram_text
from app.telegram.rate_limit import UserRateLimiter


def make_settings(**overrides) -> Settings:
    defaults = {
        "_env_file": None,
        "openai_api_key": "test-key",
        "telegram_allowed_user_ids": "42",
        "telegram_request_cooldown_seconds": 0,
        "database_path": "unused.db",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_message(*, user_id: int, text: str | None = None):
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


def test_telegram_allowlist_is_secure_by_default() -> None:
    settings = make_settings(telegram_allowed_user_ids="")

    assert is_telegram_user_allowed(42, settings) is False
    assert is_telegram_user_allowed(None, settings) is False


def test_telegram_allowlist_includes_role_lists() -> None:
    settings = make_settings(
        telegram_allowed_user_ids="42",
        telegram_admin_user_ids="77",
        telegram_manager_user_ids="88",
        telegram_observer_user_ids="99",
    )

    assert settings.allowed_telegram_user_ids == frozenset({42, 77, 88, 99})
    assert get_telegram_user_role(77, settings) is UserRole.ADMIN
    assert get_telegram_user_role(88, settings) is UserRole.MANAGER
    assert get_telegram_user_role(99, settings) is UserRole.OBSERVER
    assert get_telegram_user_role(42, settings) is UserRole.EMPLOYEE


@pytest.mark.asyncio
async def test_start_handler_denies_unknown_user(
    conversation_store: ConversationStore,
) -> None:
    message = make_message(user_id=99)
    settings = make_settings(telegram_allowed_user_ids="42")

    await start_handler(message, settings, conversation_store)

    response_text = message.answer.await_args.args[0]
    assert "Доступ" in response_text
    assert "99" in response_text


@pytest.mark.asyncio
async def test_start_handler_reports_role(
    conversation_store: ConversationStore,
) -> None:
    message = make_message(user_id=42)
    settings = make_settings(telegram_manager_user_ids="42")

    await start_handler(message, settings, conversation_store)

    response_text = message.answer.await_args.args[0]
    assert "Руководитель" in response_text


@pytest.mark.asyncio
async def test_text_handler_routes_agent_and_saves_memory(
    monkeypatch: pytest.MonkeyPatch,
    conversation_store: ConversationStore,
) -> None:
    message = make_message(user_id=42, text="Какие сделки зависли?")
    settings = make_settings(telegram_manager_user_ids="42")
    rate_limiter = UserRateLimiter(0)
    captured: dict[str, object] = {}

    async def fake_execute_agent(
        _message: str,
        _settings: Settings,
        **kwargs,
    ) -> AgentRunResult:
        captured.update(kwargs)
        return AgentRunResult(
            answer="Нужны данные CRM",
            agent="Agency Stack Deal Analyst",
            route=AgentRoute.DEAL_ANALYST,
        )

    class FakeTyping:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr("app.telegram.handlers.execute_agent", fake_execute_agent)
    monkeypatch.setattr(
        "app.telegram.handlers.ChatActionSender.typing",
        lambda **_kwargs: FakeTyping(),
    )

    await text_handler(message, settings, rate_limiter, conversation_store)

    assert captured["route"] is AgentRoute.DEAL_ANALYST
    assert captured["role"] is UserRole.MANAGER
    assert message.answer.await_args.args[0] == "Нужны данные CRM"
    assert await conversation_store.count_messages(42) == 2


@pytest.mark.asyncio
async def test_reset_handler_clears_history(
    conversation_store: ConversationStore,
) -> None:
    message = make_message(user_id=42)
    settings = make_settings()
    await conversation_store.upsert_user(
        42,
        username="user42",
        display_name="User 42",
        role=UserRole.EMPLOYEE,
    )
    await conversation_store.add_message(42, role="user", content="Вопрос")

    await reset_handler(message, settings, conversation_store)

    assert await conversation_store.count_messages(42) == 0
    assert "Удалено сообщений: 1" in message.answer.await_args.args[0]


def test_telegram_text_helpers() -> None:
    assert resolve_user_message("📚 База знаний").startswith("Объясни")
    assert resolve_user_message("Обычный запрос") == "Обычный запрос"

    chunks = split_telegram_text("12345 67890", chunk_size=7)
    assert chunks == ["12345", "67890"]
