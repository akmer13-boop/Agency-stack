from __future__ import annotations

from typing import Any, Final

from app.integrations.bitrix24.client import Bitrix24ReadOnlyClient

SYNC_EXTRA_READ_ONLY_METHODS: Final[frozenset[str]] = frozenset(
    {
        "crm.contact.list",
        "crm.company.list",
        "crm.activity.list",
    }
)


class SyncBitrix24Client(Bitrix24ReadOnlyClient):
    """Read-only client used by the local CRM synchronizer."""

    def _endpoint(self, method: str) -> str:
        if method in SYNC_EXTRA_READ_ONLY_METHODS:
            return f"{self._webhook_url}{method}.json"
        return super()._endpoint(method)

    async def list_sync_deals(self, *, max_items: int) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.deal.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
            },
            max_items=max_items,
        )

    async def list_sync_leads(self, *, max_items: int) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.lead.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
            },
            max_items=max_items,
        )

    async def list_sync_contacts(self, *, max_items: int) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.contact.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
            },
            max_items=max_items,
        )

    async def list_sync_companies(self, *, max_items: int) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.company.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
            },
            max_items=max_items,
        )

    async def list_sync_activities(self, *, max_items: int) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.activity.list",
            {
                "select": ["*"],
                "order": {"ID": "ASC"},
            },
            max_items=max_items,
        )

    async def list_sync_stage_history(
        self,
        *,
        entity_type_id: int,
        max_items: int,
    ) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.stagehistory.list",
            {
                "entityTypeId": entity_type_id,
                "select": [
                    "ID",
                    "TYPE_ID",
                    "OWNER_ID",
                    "CREATED_TIME",
                    "CATEGORY_ID",
                    "STAGE_SEMANTIC_ID",
                    "STAGE_ID",
                ],
                "order": {"ID": "ASC"},
            },
            result_key="items",
            max_items=max_items,
        )
