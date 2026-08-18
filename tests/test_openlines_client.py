import json

import httpx
import pytest

from app.integrations.bitrix24.client import Bitrix24ReadOnlyViolation
from app.integrations.bitrix24.openlines_client import OpenLinesReadOnlyClient

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


@pytest.mark.asyncio
async def test_openlines_client_reads_crm_chat_session_and_dialog_history() -> None:
    calls: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))

        if request.url.path.endswith("/imopenlines.crm.chat.get.json"):
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "CHAT_ID": "77",
                            "CONNECTOR_TITLE": "Telegram",
                        }
                    ]
                },
            )

        if request.url.path.endswith("/imopenlines.session.history.get.json"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "sessionId": "900",
                        "chatId": "77",
                        "message": {},
                    }
                },
            )

        if request.url.path.endswith("/im.dialog.messages.get.json"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "chat_id": 77,
                        "messages": [
                            {
                                "id": 100,
                                "chat_id": 77,
                                "author_id": 10,
                                "text": "hello",
                            }
                        ],
                        "users": [],
                        "files": [],
                    }
                },
            )

        raise AssertionError(f"Unexpected path: {request.url.path}")

    client = OpenLinesReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    chats = await client.get_crm_chats("lead", "123")
    history = await client.get_session_history("77")
    dialog = await client.get_dialog_messages("77", last_id=101, limit=50)

    assert chats[0]["CHAT_ID"] == "77"
    assert history["sessionId"] == "900"
    assert dialog["messages"][0]["id"] == 100

    assert calls[0][1] == {
        "CRM_ENTITY_TYPE": "lead",
        "CRM_ENTITY": 123,
        "ACTIVE_ONLY": "N",
    }
    assert calls[1][1] == {"CHAT_ID": 77}
    assert calls[2][1] == {
        "DIALOG_ID": "chat77",
        "LIMIT": 50,
        "LAST_ID": 101,
    }


@pytest.mark.asyncio
async def test_dialog_history_supports_first_id_for_incremental_sync() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload == {
            "DIALOG_ID": "chat77",
            "LIMIT": 25,
            "FIRST_ID": 100,
        }
        return httpx.Response(
            200,
            json={"result": {"chat_id": 77, "messages": [], "users": [], "files": []}},
        )

    client = OpenLinesReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_dialog_messages("77", first_id=100, limit=25)

    assert result["messages"] == []


def test_openlines_client_keeps_write_methods_blocked() -> None:
    client = OpenLinesReadOnlyClient(WEBHOOK_URL)

    with pytest.raises(Bitrix24ReadOnlyViolation):
        client._endpoint("im.message.add")

    with pytest.raises(Bitrix24ReadOnlyViolation):
        client._endpoint("imopenlines.message.add")

    with pytest.raises(Bitrix24ReadOnlyViolation):
        client._endpoint("crm.deal.update")


@pytest.mark.asyncio
async def test_crm_chat_batch_is_read_only_and_maps_per_item_results() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/batch.json")
        payload = json.loads(request.content)
        assert payload["halt"] == 0
        assert len(payload["cmd"]) == 2
        assert all(
            command.startswith("imopenlines.crm.chat.get?") for command in payload["cmd"].values()
        )
        return httpx.Response(
            200,
            json={
                "result": {
                    "result": {"q00": [{"CHAT_ID": "77"}], "q01": []},
                    "result_error": {},
                }
            },
        )

    client = OpenLinesReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )
    result = await client.get_crm_chats_batch([("lead", "123"), ("deal", "456")])
    assert len(result) == 2
    assert result[0].entity_type == "lead"
    assert result[0].entity_id == "123"
    assert result[0].chats[0]["CHAT_ID"] == "77"
    assert result[0].error_code is None
    assert result[1].entity_type == "deal"
    assert result[1].chats == ()
    assert result[1].error_code is None

    with pytest.raises(Bitrix24ReadOnlyViolation):
        client._endpoint("batch")


@pytest.mark.asyncio
async def test_crm_chat_batch_preserves_per_item_errors() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "result": {"q00": []},
                    "result_error": {
                        "q01": {
                            "error": "ACCESS_DENIED",
                            "error_description": "denied",
                        }
                    },
                }
            },
        )

    client = OpenLinesReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )
    result = await client.get_crm_chats_batch([("lead", "123"), ("deal", "456")])
    assert result[0].error_code is None
    assert result[1].error_code == "ACCESS_DENIED"
