from pathlib import Path

import pytest

from app.domain import UserRole
from app.storage.conversation_store import ConversationStore


@pytest.mark.asyncio
async def test_conversation_store_persists_and_clears_history(tmp_path: Path) -> None:
    store = ConversationStore(str(tmp_path / "agency.db"), history_limit=2)
    await store.initialize()
    await store.upsert_user(
        42,
        username="tester",
        display_name="Test User",
        role=UserRole.MANAGER,
    )

    await store.add_message(42, role="user", content="Первый вопрос")
    await store.add_message(
        42,
        role="assistant",
        content="Первый ответ",
        agent_name="Agent One",
    )
    await store.add_message(42, role="user", content="Второй вопрос")

    history = await store.get_recent_messages(42)

    assert [item.content for item in history] == ["Первый ответ", "Второй вопрос"]
    assert await store.count_messages(42) == 3

    deleted = await store.clear_history(42)

    assert deleted == 3
    assert await store.count_messages(42) == 0
