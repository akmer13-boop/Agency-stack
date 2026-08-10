import pytest

from app.integrations.bitrix24 import Bitrix24ConfigurationError
from app.integrations.bitrix24.urls import (
    BitrixEntityUrlError,
    build_bitrix_entity_url,
    build_deal_url,
    build_lead_url,
    portal_origin_from_webhook,
)

WEBHOOK = "https://b24.example.test/rest/7/supersecretcode/"


def test_portal_origin_drops_webhook_secret() -> None:
    origin = portal_origin_from_webhook(WEBHOOK)

    assert origin == "https://b24.example.test"
    assert "supersecretcode" not in origin
    assert "/rest/" not in origin


def test_build_deal_url_is_secret_free() -> None:
    url = build_deal_url(WEBHOOK, 7040)

    assert url == "https://b24.example.test/crm/deal/details/7040/"
    assert "supersecretcode" not in url
    assert "/rest/" not in url


def test_build_lead_url_is_secret_free() -> None:
    url = build_lead_url(WEBHOOK, "123")

    assert url == "https://b24.example.test/crm/lead/details/123/"
    assert "supersecretcode" not in url


@pytest.mark.parametrize("entity_id", ["", "0", "-1", "abc", 0, -5])
def test_invalid_entity_id_is_rejected(entity_id: str | int) -> None:
    with pytest.raises(BitrixEntityUrlError, match="positive integer"):
        build_deal_url(WEBHOOK, entity_id)


def test_unsupported_entity_type_is_rejected() -> None:
    with pytest.raises(BitrixEntityUrlError, match="Unsupported"):
        build_bitrix_entity_url(WEBHOOK, "contact", 1)  # type: ignore[arg-type]


def test_unsafe_webhook_is_rejected_before_url_build() -> None:
    with pytest.raises(Bitrix24ConfigurationError):
        build_deal_url("http://b24.example.test/rest/7/supersecretcode/", 1)
