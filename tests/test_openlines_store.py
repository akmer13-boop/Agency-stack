from pathlib import Path

import aiosqlite
import pytest

from app.storage.openlines_store import OpenLinesStore


@pytest.mark.asyncio
async def test_openlines_store_persists_session_roles_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    store = OpenLinesStore(database_path)
    await store.initialize()

    chat = {
        "CHAT_ID": "77",
        "CONNECTOR_ID": "telegrambot",
        "CONNECTOR_TITLE": "Telegram",
    }
    await store.upsert_chat_link(chat, entity_type="lead", entity_id="123")

    history = {
        "sessionId": "900",
        "chatId": "77",
        "message": {
            "1": {
                "id": "1",
                "senderid": "10",
                "date": "2026-08-15T10:00:00+00:00",
                "text": "Ответ менеджера",
            },
            "2": {
                "id": "2",
                "senderid": "5000",
                "date": "2026-08-15T10:01:00+00:00",
                "text": "Ответ клиента",
            },
            "3": {
                "id": "3",
                "senderid": "0",
                "date": "2026-08-15T10:02:00+00:00",
                "text": "Системное сообщение",
            },
            "4": {
                "id": "4",
                "senderid": "6000",
                "date": "2026-08-15T10:03:00+00:00",
                "text": "Сообщение бота",
            },
        },
        "users": {
            "5000": {
                "id": "5000",
                "type": "extranet",
                "connector": True,
                "externalAuthId": "imconnector",
            },
            "6000": {"id": "6000", "bot": True},
        },
        "files": {"1": {"id": "1"}},
    }

    first = await store.upsert_history(
        "77",
        history,
        directory_user_ids=frozenset({"10"}),
    )
    second = await store.upsert_history(
        "77",
        history,
        directory_user_ids=frozenset({"10"}),
    )

    assert first.messages_written == 4
    assert second.messages_written == 4

    counts = await store.counts()
    assert counts.messages == 4
    assert counts.manager_messages == 1
    assert counts.client_messages == 1
    assert counts.system_messages == 1
    assert counts.bot_messages == 1
    assert counts.unknown_messages == 0


@pytest.mark.asyncio
async def test_dialog_backfill_preserves_native_session_and_adds_unknown_role(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "agency.db")
    store = OpenLinesStore(database_path)
    await store.initialize()
    await store.upsert_chat_link(
        {"CHAT_ID": "77"},
        entity_type="lead",
        entity_id="123",
    )

    await store.upsert_history(
        "77",
        {
            "sessionId": "900",
            "chatId": "77",
            "message": {
                "100": {
                    "id": "100",
                    "senderid": "10",
                    "date": "2026-08-15T10:00:00+00:00",
                    "text": "Последняя сессия",
                }
            },
            "users": {},
            "files": {},
            "chat": {"77": {"id": "77", "messageCount": 3}},
        },
        directory_user_ids=frozenset({"10"}),
    )

    page = {
        "chat_id": 77,
        "messages": [
            {
                "id": 100,
                "chat_id": 77,
                "author_id": 10,
                "date": "2026-08-15T10:00:00+00:00",
                "text": "Последняя сессия",
            },
            {
                "id": 50,
                "chat_id": 77,
                "author_id": 5000,
                "date": "2026-08-01T10:00:00+00:00",
                "text": "Старый клиент",
            },
            {
                "id": 40,
                "chat_id": 77,
                "author_id": 7777,
                "date": "2026-07-01T10:00:00+00:00",
                "text": "Неизвестный отправитель",
            },
        ],
        "users": [
            {"id": 10, "type": "user"},
            {"id": 5000, "type": "extranet"},
            {"id": 7777, "type": "user"},
        ],
        "files": [],
    }

    await store.upsert_dialog_page(
        "77",
        page,
        directory_user_ids=frozenset({"10"}),
        expected_message_count=3,
    )

    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT
                message_id,
                session_id,
                sender_role,
                message_source,
                session_binding_kind
            FROM openlines_messages
            ORDER BY CAST(message_id AS INTEGER) DESC
            """
        )
        rows = await cursor.fetchall()

    assert rows[0] == ("100", "900", "manager", "session_history", "native")
    assert rows[1][0] == "50"
    assert rows[1][2] == "client"
    assert rows[1][3] == "dialog_history"
    assert rows[1][4] == "chat_history"
    assert rows[2][2] == "unknown"


@pytest.mark.asyncio
async def test_chat_sync_state_tracks_resumable_bounds(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agency.db")
    store = OpenLinesStore(database_path)
    await store.initialize()
    await store.upsert_chat_link(
        {"CHAT_ID": "77"},
        entity_type="lead",
        entity_id="123",
    )

    await store.upsert_dialog_page(
        "77",
        {
            "chat_id": 77,
            "messages": [
                {"id": 100, "author_id": 10, "text": "A"},
                {"id": 51, "author_id": 5000, "text": "B"},
            ],
            "users": [
                {"id": 10, "type": "user"},
                {"id": 5000, "type": "extranet"},
            ],
            "files": [],
        },
        directory_user_ids=frozenset({"10"}),
        expected_message_count=3,
    )

    state = await store.update_chat_sync_state(
        "77",
        expected_message_count=3,
        pages_added=1,
    )

    assert state.oldest_message_id == 51
    assert state.newest_message_id == 100
    assert state.stored_message_count == 2
    assert state.pages_loaded == 1
    assert state.backfill_complete is False

    await store.upsert_dialog_page(
        "77",
        {
            "chat_id": 77,
            "messages": [{"id": 1, "author_id": 0, "text": "system"}],
            "users": [],
            "files": [],
        },
        directory_user_ids=frozenset({"10"}),
        expected_message_count=3,
    )

    state = await store.update_chat_sync_state(
        "77",
        expected_message_count=3,
        pages_added=1,
    )

    assert state.stored_message_count == 3
    assert state.pages_loaded == 2
    assert state.backfill_complete is True
