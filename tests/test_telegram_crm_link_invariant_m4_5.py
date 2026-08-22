from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ParseMode

from app.config import Settings
from app.telegram.handlers import _send_long_text as send_bitrix_report
from app.telegram.rich_text import render_safe_crm_links_html
from app.telegram.rop_handlers import _send_long_text as send_rop_report
from app.telegram.rop_mvp_dashboard_handlers import _send_dashboard_text
from app.telegram.rop_scheduler import _send_report as send_scheduled_report


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        bitrix24_webhook_url=(
            "https://b24.example.test/rest/7/supersecretcode/"
        ),
    )


def test_plain_typed_lead_and_deal_references_are_linked() -> None:
    rendered = render_safe_crm_links_html(
        "Проверь Лид #123 и сделку #7040. Менеджер #42 не карточка.",
        _settings(),
    )

    assert rendered is not None
    assert "crm/lead/details/123/" in rendered
    assert "crm/deal/details/7040/" in rendered
    assert "Менеджер #42" in rendered
    assert "supersecretcode" not in rendered


def test_bare_number_is_not_linked_without_entity_type() -> None:
    assert render_safe_crm_links_html("Проверь #7040", _settings()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sender",
    (send_bitrix_report, send_rop_report),
)
async def test_direct_reports_embed_typed_crm_cards(sender) -> None:
    message = SimpleNamespace(answer=AsyncMock())

    await sender(message, "Лид #123 · Сделка #7040", _settings())

    sent = message.answer.await_args
    assert sent.kwargs["parse_mode"] is ParseMode.HTML
    assert "crm/lead/details/123/" in sent.args[0]
    assert "crm/deal/details/7040/" in sent.args[0]
    assert "supersecretcode" not in sent.args[0]


@pytest.mark.asyncio
async def test_dashboard_uses_the_same_card_link_rule() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    await _send_dashboard_text(message, "Проверь Сделка #7444", _settings())

    sent = message.answer.await_args
    assert sent.kwargs["parse_mode"] is ParseMode.HTML
    assert "crm/deal/details/7444/" in sent.args[0]
    assert "supersecretcode" not in sent.args[0]


@pytest.mark.asyncio
async def test_scheduled_report_uses_the_same_card_link_rule() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())

    await send_scheduled_report(bot, 42, "Проверь Сделка #7040", _settings())

    sent = bot.send_message.await_args
    assert sent.kwargs["parse_mode"] is ParseMode.HTML
    assert sent.kwargs["chat_id"] == 42
    assert "crm/deal/details/7040/" in sent.kwargs["text"]
    assert "supersecretcode" not in sent.kwargs["text"]
