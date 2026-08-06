import json

import httpx
import pytest

from app.integrations.bitrix24.inventory_client import InventoryBitrix24Client
from app.services.bitrix24_inventory import (
    format_bitrix24_inventory,
    inspect_bitrix24,
)

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


@pytest.mark.asyncio
async def test_inventory_uses_method_get_and_counts_custom_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path.endswith("/method.get.json"):
            method = payload["name"]
            available = method in {
                "method.get",
                "crm.deal.list",
                "crm.deal.fields",
                "crm.lead.fields",
            }
            exists = method != "imopenlines.session.history.get"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "isExisting": exists,
                        "isAvailable": available,
                    }
                },
            )
        if request.url.path.endswith("/crm.deal.fields.json"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "ID": {"type": "integer"},
                        "TITLE": {"type": "string"},
                        "UF_CRM_DEMO": {"type": "string"},
                    }
                },
            )
        if request.url.path.endswith("/crm.lead.fields.json"):
            return httpx.Response(
                200,
                json={"result": {"ID": {}, "UF_CRM_LEAD_TEST": {}}},
            )
        raise AssertionError(f"Unexpected request: {request.url.path}")

    client = InventoryBitrix24Client(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    inventory = await inspect_bitrix24(client)
    text = format_bitrix24_inventory(inventory)

    assert inventory.source == "method.get"
    assert "Доступно методов:" in text
    assert "пользовательских 1" in text
    assert "История Открытых линий — метода нет" in text
    assert "Данные CRM в OpenAI не передавались" in text


@pytest.mark.asyncio
async def test_inventory_falls_back_to_deprecated_methods_list() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/method.get.json"):
            return httpx.Response(404, json={"error": "METHOD_NOT_FOUND"})
        if request.url.path.endswith("/methods.json"):
            return httpx.Response(
                200,
                json={
                    "result": [
                        "crm.deal.list",
                        "crm.deal.fields",
                    ]
                },
            )
        if request.url.path.endswith("/crm.deal.fields.json"):
            return httpx.Response(200, json={"result": {"ID": {}}})
        raise AssertionError(f"Unexpected request: {request.url.path}")

    client = InventoryBitrix24Client(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    inventory = await inspect_bitrix24(client)
    text = format_bitrix24_inventory(inventory)

    assert inventory.source == "methods"
    assert "Старая коробка" in text
    assert "Сделки — доступен" in text
