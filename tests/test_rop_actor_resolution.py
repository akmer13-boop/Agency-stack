from __future__ import annotations

import pytest

from app.services.rop_actor_resolution import (
    ActorKind,
    build_actor_resolution_report,
    format_actor_resolution_for_ai,
)
from app.storage.crm_store import CrmStore, CrmTombstone


@pytest.mark.asyncio
async def test_actor_resolution_separates_directory_special_and_unresolved(tmp_path) -> None:
    database_path = str(tmp_path / "actors.db")
    store = CrmStore(database_path)
    await store.initialize()

    await store.upsert_entities("user", [{"ID": "1", "NAME": "Anna", "ACTIVE": True}])
    await store.upsert_entities(
        "deal",
        [{"ID": "10", "ASSIGNED_BY_ID": "1", "STAGE_ID": "NEW"}],
    )
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "20",
                "ASSIGNED_BY_ID": "7912",
                "CREATED_BY_ID": "7912",
                "SOURCE_ID": "5|WZ_TELEGRAM_TEST",
                "STATUS_ID": "NEW",
            },
            {
                "ID": "21",
                "ASSIGNED_BY_ID": "125",
                "CREATED_BY_ID": "7912",
                "SOURCE_ID": "28",
                "STATUS_ID": "NEW",
            },
        ],
    )
    await store.upsert_entities(
        "activity",
        [
            {
                "ID": "30",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "20",
                "RESPONSIBLE_ID": "7912",
                "AUTHOR_ID": "7912",
                "PROVIDER_ID": "IMOPENLINES_SESSION",
                "TYPE_ID": "6",
                "COMPLETED": "Y",
                "CREATED": "2026-08-14T09:00:00Z",
            },
            {
                "ID": "31",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "21",
                "RESPONSIBLE_ID": "484",
                "AUTHOR_ID": "54",
                "PROVIDER_ID": "VOXIMPLANT_CALL",
                "TYPE_ID": "2",
                "COMPLETED": "Y",
                "CREATED": "2026-08-14T09:10:00Z",
            },
            {
                "ID": "32",
                "OWNER_TYPE_ID": "2",
                "OWNER_ID": "10",
                "RESPONSIBLE_ID": "102",
                "AUTHOR_ID": "102",
                "PROVIDER_ID": "CRM_TODO",
                "TYPE_ID": "6",
                "COMPLETED": "Y",
                "CREATED": "2026-08-14T09:20:00Z",
            },
            {
                "ID": "33",
                "OWNER_TYPE_ID": "1",
                "OWNER_ID": "20",
                "RESPONSIBLE_ID": "9999",
                "AUTHOR_ID": "9999",
                "PROVIDER_ID": "IMOPENLINES_SESSION",
                "TYPE_ID": "6",
                "COMPLETED": "Y",
                "CREATED": "2026-08-14T09:30:00Z",
            },
        ],
    )
    await store.apply_tombstones(
        [
            CrmTombstone(
                entity_type="activity",
                entity_id="33",
                source_audit_run_id=10,
                evidence_kind="test_missing",
                evidence_verified_at="2026-08-14T10:00:00Z",
            )
        ]
    )

    report = await build_actor_resolution_report(database_path)
    actors = {item.actor_id: item for item in report.actors}

    assert actors["1"].kind is ActorKind.DIRECTORY_USER
    assert actors["7912"].kind is ActorKind.SPECIAL_ACTOR_CANDIDATE
    assert "openlines_self_authored" in actors["7912"].technical_signals
    assert "open_channel_lead_creator" in actors["7912"].technical_signals

    assert actors["484"].kind is ActorKind.UNRESOLVED_ACTOR
    assert actors["484"].technical_signals == ("telephony_related",)

    assert actors["102"].kind is ActorKind.UNRESOLVED_ACTOR
    assert actors["102"].technical_signals == ("crm_todo_related",)

    assert actors["125"].kind is ActorKind.UNRESOLVED_ACTOR
    assert actors["125"].technical_signals == ()
    assert "9999" not in actors


@pytest.mark.asyncio
async def test_actor_resolution_formatter_is_conservative(tmp_path) -> None:
    database_path = str(tmp_path / "actors.db")
    store = CrmStore(database_path)
    await store.initialize()
    await store.upsert_entities(
        "lead",
        [
            {
                "ID": "20",
                "ASSIGNED_BY_ID": "7912",
                "CREATED_BY_ID": "7912",
                "SOURCE_ID": "5|WZ_TELEGRAM_TEST",
                "STATUS_ID": "NEW",
            }
        ],
    )

    report = await build_actor_resolution_report(database_path)
    text = format_actor_resolution_for_ai(report)

    assert "special_actor_candidate" in text
    assert "not proof that the actor is a bot" in text
    assert "Do not call special_actor_candidate a confirmed bot" in text
    assert "Do not call unresolved_actor deleted" in text
    assert "No CRM write" in text
