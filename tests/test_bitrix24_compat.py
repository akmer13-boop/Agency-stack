import json

import httpx
import pytest

from app.integrations.bitrix24 import Bitrix24RequestError
from app.integrations.bitrix24.compatible_client import CompatibleBitrix24ReadOnlyClient

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


@pytest.mark.asyncio
async def test_deal_list_uses_only_fields_supported_by_boxed_portal() -> None:
    requested_methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_methods.append(request.url.path)
        if request.url.path.endswith("/crm.deal.fields.json"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "ID": {"type": "integer"},
                        "CATEGORY_ID": {"type": "crm_category"},
                        "STAGE_ID": {"type": "crm_status"},
                        "OPPORTUNITY": {"type": "double"},
                        "ASSIGNED_BY_ID": {"type": "user"},
                        "DATE_MODIFY": {"type": "datetime"},
                    }
                },
            )

        payload = json.loads(request.content)
        assert request.url.path.endswith("/crm.deal.list.json")
        assert set(payload["select"]) == {
            "ID",
            "CATEGORY_ID",
            "STAGE_ID",
            "OPPORTUNITY",
            "ASSIGNED_BY_ID",
            "DATE_MODIFY",
        }
        assert "MOVED_TIME" not in payload["select"]
        assert "filter" not in payload
        return httpx.Response(200, json={"result": [{"ID": "15"}]})

    client = CompatibleBitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    deals = await client.list_deals(max_items=10)

    assert deals == [{"ID": "15"}]
    assert requested_methods == [
        "/rest/7/supersecretcode/crm.deal.fields.json",
        "/rest/7/supersecretcode/crm.deal.list.json",
    ]


@pytest.mark.asyncio
async def test_user_directory_failure_does_not_block_reports() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/user.get.json")
        return httpx.Response(403, json={"error": "ACCESS_DENIED"})

    client = CompatibleBitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    users = await client.list_users()

    assert users == []


@pytest.mark.asyncio
async def test_deal_permission_error_is_explained_safely() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "ACCESS_DENIED"})

    client = CompatibleBitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(Bitrix24RequestError, match="право просмотра CRM-сделок") as error:
        await client.list_deals(max_items=10)

    assert error.value.error_code == "HTTP_403"
    assert "supersecretcode" not in str(error.value)
