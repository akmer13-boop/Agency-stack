from app.config import Settings
from app.telegram.rich_text import render_safe_crm_links_html

WEBHOOK = "https://b24.example.test/rest/7/supersecretcode/"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK,
    )


def test_trusted_crm_markdown_becomes_safe_telegram_link() -> None:
    text = (
        "Проверь [Лид #123]"
        "(https://b24.example.test/crm/lead/details/123/) & <факт>"
    )

    rendered = render_safe_crm_links_html(
        text,
        _settings(),
    )

    assert rendered is not None
    assert (
        '<a href="https://b24.example.test/crm/lead/details/123/">'
        "Лид #123</a>"
    ) in rendered
    assert "&amp; &lt;факт&gt;" in rendered
    assert "supersecretcode" not in rendered
    assert "/rest/" not in rendered


def test_deal_card_number_can_be_clickable_too() -> None:
    rendered = render_safe_crm_links_html(
        "[Сделка #7040]"
        "(https://b24.example.test/crm/deal/details/7040/)",
        _settings(),
    )

    assert rendered == (
        '<a href="https://b24.example.test/crm/deal/details/7040/">'
        "Сделка #7040</a>"
    )


def test_external_or_mismatched_links_are_not_activated() -> None:
    external = render_safe_crm_links_html(
        "[Лид #123](https://evil.example/crm/lead/details/123/)",
        _settings(),
    )
    mismatched = render_safe_crm_links_html(
        "[Лид #123]"
        "(https://b24.example.test/crm/lead/details/999/)",
        _settings(),
    )

    assert external is None
    assert mismatched is None


def test_no_bitrix_configuration_keeps_plain_delivery() -> None:
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url="",
    )

    assert (
        render_safe_crm_links_html(
            "[Лид #123](https://b24.example.test/crm/lead/details/123/)",
            settings,
        )
        is None
    )
