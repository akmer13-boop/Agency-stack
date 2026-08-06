import json

import httpx
import pytest

from app.integrations.bitrix24.client import (
    DEAL_ANALYTICS_FIELDS,
    Bitrix24ConfigurationError,
    Bitrix24ReadOnlyClient,
    Bitrix24ReadOnlyViolation,
    Bitrix24RequestError,
)

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


def test_webhook_url_requires_https() -> None:
    with pytest.raises(Bitrix24ConfigurationError, match="HTTPS"):
        Bitrix24ReadOnlyClient("http://b24.example.test/rest/7/supersecretcode/")


def test_write_method_is_blocked_before_network_call() -> None:
    client = Bitrix24ReadOnlyClient(WEBHOOK_URL)

    with pytest.raises(Bitrix24ReadOnlyViolation, match="read-only"):
        client._endpoint("crm.deal.update")


@pytest.mark.asyncio
async def test_profile_uses_normalized_webhook_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{WEBHOOK_URL}profile.json"
        assert json.loads(request.content) == {}
        return httpx.Response(200, json={"result": {"ID": "7", "ADMIN": False}})

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    profile = await client.profile()

    assert profile == {"ID": "7", "ADMIN": False}
    assert client.portal_host == "b24.example.test"


@pytest.mark.asyncio
async def test_list_deals_paginates_and_uses_minimal_fields() -> None:
    starts: list[int] = []
    selected_fields: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        starts.append(payload["start"])
        selected_fields.extend(payload["select"])
        if payload["start"] == 0:
            return httpx.Response(
                200,
                json={"result": [{"ID": "1"}], "next": 50},
            )
        return httpx.Response(200, json={"result": [{"ID": "2"}]})

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    deals = await client.list_deals(max_items=10)

    assert deals == [{"ID": "1"}, {"ID": "2"}]
    assert starts == [0, 50]
    assert set(selected_fields) == set(DEAL_ANALYTICS_FIELDS)
    assert "COMMENTS" not in selected_fields
    assert "CONTACT_ID" not in selected_fields


@pytest.mark.asyncio
async def test_webhook_secret_is_not_exposed_in_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "NO_AUTH_FOUND"})

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(Bitrix24RequestError) as error:
        await client.profile()

    assert "supersecretcode" not in str(error.value)
