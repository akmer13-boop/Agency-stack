from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

import aiosqlite

from app.semantic.models import (
    SemanticActivity,
    SemanticDeal,
    SemanticLead,
    SemanticMappingError,
    SemanticStageEvent,
    SemanticUser,
)
from app.semantic.normalizer import (
    normalize_activity,
    normalize_deal,
    normalize_lead,
    normalize_stage_event,
    normalize_user,
)

T = TypeVar("T")


class SemanticRepository:
    """Read-only semantic projection over crm_raw_entities."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    async def _load(
        self,
        entity_type: str,
        normalizer: Callable[[dict[str, Any]], T],
        *,
        limit: int | None = None,
    ) -> list[T]:
        query = """
            SELECT entity_id, payload_json
            FROM crm_raw_entities
            WHERE entity_type = ?
            ORDER BY entity_id
        """
        params: list[Any] = [entity_type]

        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            query += " LIMIT ?"
            params.append(limit)

        async with aiosqlite.connect(self.database_path) as database:
            cursor = await database.execute(query, params)
            rows = await cursor.fetchall()

        result: list[T] = []

        for stored_id, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise SemanticMappingError(
                    f"{entity_type}:{stored_id}: invalid payload_json"
                ) from exc

            if not isinstance(payload, dict):
                raise SemanticMappingError(
                    f"{entity_type}:{stored_id}: payload_json is not an object"
                )

            payload_id = payload.get("ID")
            if payload_id is not None and str(payload_id) != str(stored_id):
                raise SemanticMappingError(
                    f"{entity_type}:{stored_id}: stored ID does not match payload ID"
                )

            result.append(normalizer(payload))

        return result

    async def leads(self, *, limit: int | None = None) -> list[SemanticLead]:
        return await self._load("lead", normalize_lead, limit=limit)

    async def deals(self, *, limit: int | None = None) -> list[SemanticDeal]:
        return await self._load("deal", normalize_deal, limit=limit)

    async def activities(self, *, limit: int | None = None) -> list[SemanticActivity]:
        return await self._load("activity", normalize_activity, limit=limit)

    async def users(self, *, limit: int | None = None) -> list[SemanticUser]:
        return await self._load("user", normalize_user, limit=limit)

    async def lead_stage_history(
        self,
        *,
        limit: int | None = None,
    ) -> list[SemanticStageEvent]:
        return await self._load(
            "lead_stage_history",
            lambda payload: normalize_stage_event(
                payload,
                entity_type="lead_stage_history",
            ),
            limit=limit,
        )

    async def deal_stage_history(
        self,
        *,
        limit: int | None = None,
    ) -> list[SemanticStageEvent]:
        return await self._load(
            "deal_stage_history",
            lambda payload: normalize_stage_event(
                payload,
                entity_type="deal_stage_history",
            ),
            limit=limit,
        )
