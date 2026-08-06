from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services.agent_runner import AgentRunResult
from app.telegram.access import is_telegram_user_allowed
from app.telegram.handlers import start_handler, text_handler
from app.telegram.messages import resolve_user_message, split_telegram_text
from app.telegram.rate_limit import UserRateLimiter


def make_message(*, user_id: int, text: str | None = None):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        text=text,
        answer=AsyncMock(),
        bot=object(),
        chat=SimpleNamespace(id=100),
    )


def test_telegram_allowlist_is_secure_by_default() -> None:
    settings = Settings(telegram_allowed_user_ids="")

    assert is_telegram_user_allowed(42, settings) is False
    assert is_telegram_user_allowed(None, settings) is False


def test_telegram_allowlist_parses_comma_separated_ids() -> None:
    settings = Settings(telegram_allowed_user_ids="42, 77")

    assert settings.allowed_telegram_user_ids == frozenset({42, 77})
    assert is_telegram_user_allowed(42, settings) is True
    assert is_telegram_user_allowed(99, settings) is False


@pytest.mark.asyncio
async def test_start_handler_denies_unknown_user() -> None:
    message = make_message(user_id=99)
    settings = Settings(telegram_allowed_user_ids="42")

    await start_handler(message, settings)

    response_text = message.answer.await_args.args[0]
    assert "Доступ" in response_text
    assert "99" in response_text


@pytest.mark.asyncio
async def test_text_handler_runs_agent_for_allowed_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = make_message(user_id=42, text="Привет")
    settings = Settings(
        openai_api_key="test-key",
        telegram_allowed_user_ids="42",
        telegram_request_cooldown_seconds=0,
    )
    rate_limiter = UserRateLimiter(0)

    async def fake_execute_agent(_message: str, _settings: Settings) -> AgentRunResult:
        return AgentRunResult(
            answer="Оркестратор отвечает",
            agent="Agency Stack Orchestrator",
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

    await text_handler(message, settings, rate_limiter)

    message.answer.assert_awaited_once_with("Оркестратор отвечает")


def test_telegram_text_helpers() -> None:
    assert resolve_user_message("📚 База знаний").startswith("Объясни")
    assert resolve_user_message("Обычный запрос") == "Обычный запрос"

    chunks = split_telegram_text("12345 67890", chunk_size=7)
    assert chunks == ["12345", "67890"]
