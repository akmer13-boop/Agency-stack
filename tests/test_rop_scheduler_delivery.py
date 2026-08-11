from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.telegram import rop_scheduler as scheduler_module


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_scheduler_delivery_is_durable_across_ticks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_daily(_settings: Settings) -> str:
        return "daily report"

    monkeypatch.setattr(scheduler_module, "build_rop_daily", fake_daily)

    settings = Settings(
        _env_file=None,
        rop_scheduler_enabled=True,
        rop_scheduler_daily_enabled=True,
        rop_scheduler_daily_time="08:00",
        rop_scheduler_recipient_ids="100,200",
        rop_scheduler_state_path=str(tmp_path / "scheduler.json"),
        telegram_manager_user_ids="100,200",
        rop_timezone="UTC",
    )
    bot = FakeBot()
    now = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)

    first = await scheduler_module.run_rop_scheduler_tick(
        bot,  # type: ignore[arg-type]
        settings,
        now=now,
    )
    assert first.due == 2
    assert first.delivered == 2
    assert first.failed == 0
    assert bot.messages == [(100, "daily report"), (200, "daily report")]

    second = await scheduler_module.run_rop_scheduler_tick(
        bot,  # type: ignore[arg-type]
        settings,
        now=now,
    )
    assert second.due == 0
    assert second.delivered == 0
    assert len(bot.messages) == 2
    assert (tmp_path / "scheduler.json").exists()
