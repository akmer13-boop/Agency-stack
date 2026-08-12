from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.integrations.bitrix24 import Bitrix24ReadOnlyClient
from app.services.bitrix24_reconciliation import (
    BitrixReconciliationAudit,
    BitrixReconciliationAuditStatus,
    EntityReconciliationEvidence,
)
from app.services.bitrix24_tombstone_activation import (
    BitrixTombstoneActivationBlocked,
    TombstoneVerificationStatus,
    activate_bitrix_tombstones,
)
from app.storage.crm_store import CrmStore

WEBHOOK_URL = "https://b24.example.test/rest/7/supersecretcode/"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "crm.db"),
        bitrix24_webhook_url=WEBHOOK_URL,
        bitrix24_sync_retry_attempts=1,
        bitrix24_sync_retry_backoff_seconds=0.1,
    )


def _evidence(
    entity_type: str,
    ids: tuple[str, ...],
) -> EntityReconciliationEvidence:
    return EntityReconciliationEvidence(
        entity_type=entity_type,
        observed_count=0,
        local_count=len(ids),
        absence_candidate_count=len(ids),
        absence_candidate_ids=ids,
        observed_not_persisted_count=0,
        observed_not_persisted_ids=(),
    )


def _audit(
    *evidence: EntityReconciliationEvidence,
    authoritative: bool = True,
    status: BitrixReconciliationAuditStatus = BitrixReconciliationAuditStatus.COMPLETE,
) -> BitrixReconciliationAudit:
    return BitrixReconciliationAudit(
        run_id=8,
        created_at="2026-08-12T10:00:00+00:00",
        status=status,
        authoritative=authoritative,
        reason="full_unlimited_sync_completed",
        entity_evidence=tuple(evidence),
    )


@pytest.mark.asyncio
async def test_probe_preserves_non_2xx_json_for_safe_classification() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "", "error_description": "Not found"},
        )

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    probe = await client.probe("crm.deal.get", {"id": 8479})

    assert probe.status_code == 400
    assert probe.data["error_description"] == "Not found"


@pytest.mark.asyncio
async def test_activation_applies_only_confirmed_missing_and_preserves_raw(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CrmStore(settings.database_path)
    await store.initialize()

    await store.upsert_entities("deal", [{"ID": "8479", "TITLE": "Old"}])
    await store.upsert_entities("contact", [{"ID": "11355", "NAME": "Old"}])
    await store.upsert_entities("activity", [{"ID": "99677"}])
    await store.upsert_entities("activity", [{"ID": "123537"}])

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path.endswith("/crm.deal.get.json"):
            return httpx.Response(
                400,
                json={"error": "", "error_description": "Not found"},
            )
        if request.url.path.endswith("/crm.contact.get.json"):
            return httpx.Response(
                400,
                json={"error": "", "error_description": "Not found"},
            )
        if request.url.path.endswith("/crm.activity.list.json"):
            entity_id = str(payload["filter"]["ID"])
            if entity_id == "123537":
                return httpx.Response(200, json={"result": [{"ID": "123537"}]})
            if entity_id == "99677":
                return httpx.Response(200, json={"result": []})
        raise AssertionError(f"unexpected request: {request.url}")

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    result = await activate_bitrix_tombstones(
        settings,
        _audit(
            _evidence("deal", ("8479",)),
            _evidence("contact", ("11355",)),
            _evidence("activity", ("99677",)),
        ),
        client=client,
        store=store,
    )

    assert result.candidate_count == 3
    assert result.newly_tombstoned == 3
    assert result.already_tombstoned == 0
    assert result.verification_counts == {"confirmed_missing": 3}
    assert await store.count_tombstones() == {
        "activity": 1,
        "contact": 1,
        "deal": 1,
    }

    assert (await store.count_by_type())["deal"] == 1
    active = await store.count_active_by_type()
    assert active.get("deal", 0) == 0
    assert active.get("contact", 0) == 0
    assert active.get("activity", 0) == 1


@pytest.mark.asyncio
async def test_activation_blocks_entire_batch_when_one_candidate_exists(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CrmStore(settings.database_path)
    await store.initialize()
    await store.upsert_entities("deal", [{"ID": "8479"}])
    await store.upsert_entities("contact", [{"ID": "11355"}])

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/crm.deal.get.json"):
            return httpx.Response(
                400,
                json={"error": "", "error_description": "Not found"},
            )
        if request.url.path.endswith("/crm.contact.get.json"):
            return httpx.Response(200, json={"result": {"ID": "11355"}})
        raise AssertionError(f"unexpected request: {request.url}")

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(BitrixTombstoneActivationBlocked) as error:
        await activate_bitrix_tombstones(
            settings,
            _audit(
                _evidence("deal", ("8479",)),
                _evidence("contact", ("11355",)),
            ),
            client=client,
            store=store,
        )

    statuses = {item.status for item in error.value.verifications}
    assert TombstoneVerificationStatus.EXISTS in statuses
    assert await store.count_tombstones() == {}


@pytest.mark.asyncio
async def test_activation_blocks_entire_batch_on_access_denied(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CrmStore(settings.database_path)
    await store.initialize()
    await store.upsert_entities("deal", [{"ID": "8479"}])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": "ACCESS_DENIED", "error_description": "Access denied"},
        )

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(BitrixTombstoneActivationBlocked) as error:
        await activate_bitrix_tombstones(
            settings,
            _audit(_evidence("deal", ("8479",))),
            client=client,
            store=store,
        )

    assert error.value.verifications[0].status is TombstoneVerificationStatus.ACCESS_DENIED
    assert await store.count_tombstones() == {}


@pytest.mark.asyncio
async def test_activity_candidates_fail_closed_when_positive_control_fails(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CrmStore(settings.database_path)
    await store.initialize()
    await store.upsert_entities("activity", [{"ID": "99677"}])
    await store.upsert_entities("activity", [{"ID": "123537"}])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": []})

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(BitrixTombstoneActivationBlocked, match="positive control"):
        await activate_bitrix_tombstones(
            settings,
            _audit(_evidence("activity", ("99677",))),
            client=client,
            store=store,
        )

    assert await store.count_tombstones() == {}


@pytest.mark.asyncio
async def test_non_authoritative_audit_cannot_activate_tombstones(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CrmStore(settings.database_path)
    await store.initialize()
    await store.upsert_entities("deal", [{"ID": "8479"}])

    client = Bitrix24ReadOnlyClient(
        WEBHOOK_URL,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"result": {"ID": "8479"}})
        ),
    )

    with pytest.raises(BitrixTombstoneActivationBlocked, match="not authoritative"):
        await activate_bitrix_tombstones(
            settings,
            _audit(
                _evidence("deal", ("8479",)),
                authoritative=False,
            ),
            client=client,
            store=store,
        )

    assert await store.count_tombstones() == {}
