from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, Final

from app.integrations.bitrix24.client import (
    Bitrix24ReadOnlyClient,
    Bitrix24RequestError,
)

logger = logging.getLogger(__name__)

SYNC_EXTRA_READ_ONLY_METHODS: Final[frozenset[str]] = frozenset(
    {
        "crm.contact.list",
        "crm.company.list",
        "crm.activity.list",
    }
)

RETRYABLE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "HTTP_429",
        "HTTP_500",
        "HTTP_502",
        "HTTP_503",
        "HTTP_504",
        "QUERY_LIMIT_EXCEEDED",
        "OPERATION_TIME_LIMIT",
        "OVERLOAD_LIMIT",
    }
)

BITRIX_LIST_PAGE_SIZE: Final[int] = 50


class SyncBitrix24Client(Bitrix24ReadOnlyClient):
    """Read-only client used by the local CRM synchronizer."""

    def __init__(
        self,
        *args: Any,
        retry_attempts: int = 4,
        retry_backoff_seconds: float = 2.0,
        page_delay_seconds: float = 0.25,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._retry_attempts = max(1, retry_attempts)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._page_delay_seconds = max(0.0, page_delay_seconds)

    def _endpoint(self, method: str) -> str:
        if method in SYNC_EXTRA_READ_ONLY_METHODS:
            return f"{self._webhook_url}{method}.json"
        return super()._endpoint(method)

    async def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Bitrix24RequestError | None = None

        for attempt in range(1, self._retry_attempts + 1):
            try:
                return await super().call(method, params)
            except Bitrix24RequestError as exc:
                last_error = exc
                error_code = (exc.error_code or "").upper()
                retryable = not error_code or error_code in RETRYABLE_ERROR_CODES
                if attempt >= self._retry_attempts or not retryable:
                    raise

                delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Retrying Bitrix24 sync request",
                    extra={
                        "event": "bitrix24_sync_retry",
                        "method": method,
                        "attempt": attempt,
                        "retry_in_seconds": round(delay, 2),
                        "error_code": error_code or "REQUEST_ERROR",
                    },
                )
                if delay:
                    await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        raise Bitrix24RequestError("Bitrix24 sync request failed")

    @staticmethod
    def _extract_page(
        response: dict[str, Any],
        *,
        result_key: str | None,
    ) -> list[dict[str, Any]]:
        result = response.get("result")
        if result_key is not None:
            if not isinstance(result, dict):
                raise Bitrix24RequestError("Bitrix24 returned an invalid list container")
            page_items = result.get(result_key, [])
        else:
            page_items = result

        if not isinstance(page_items, list):
            raise Bitrix24RequestError("Bitrix24 returned an invalid list response")
        if any(not isinstance(item, dict) for item in page_items):
            raise Bitrix24RequestError("Bitrix24 returned invalid list items")
        return page_items

    async def _iter_id_cursor_pages(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        result_key: str | None = None,
        max_items: int | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Stream a large list using Bitrix24's recommended stable-ID cursor pattern."""
        base_params = dict(params or {})
        base_filter = dict(base_params.get("filter") or {})
        base_params.pop("filter", None)

        last_id = 0
        yielded = 0

        for _page_number in range(self._max_pages):
            payload = dict(base_params)
            page_filter = dict(base_filter)
            if last_id:
                page_filter[">ID"] = last_id
            if page_filter:
                payload["filter"] = page_filter
            payload["start"] = -1

            response = await self.call(method, payload)
            page_items = self._extract_page(response, result_key=result_key)
            if not page_items:
                return

            ids: list[int] = []
            for item in page_items:
                raw_id = item.get("ID")
                try:
                    item_id = int(raw_id)
                except (TypeError, ValueError) as exc:
                    raise Bitrix24RequestError(
                        "Bitrix24 returned an invalid ID during cursor pagination"
                    ) from exc
                ids.append(item_id)

            if ids != sorted(ids):
                raise Bitrix24RequestError("Bitrix24 cursor page is not ordered by ID")

            new_last_id = ids[-1]
            if new_last_id <= last_id:
                raise Bitrix24RequestError("Bitrix24 cursor pagination stopped advancing")

            clean_page = page_items
            if max_items is not None:
                remaining = max_items - yielded
                if remaining <= 0:
                    return
                clean_page = clean_page[:remaining]

            if clean_page:
                yielded += len(clean_page)
                yield clean_page

            if max_items is not None and yielded >= max_items:
                return

            last_id = new_last_id
            if len(page_items) < BITRIX_LIST_PAGE_SIZE:
                return

            if self._page_delay_seconds:
                await asyncio.sleep(self._page_delay_seconds)

        raise Bitrix24RequestError(
            "Bitrix24 cursor pagination safety limit was reached",
            error_code="SYNC_PAGE_SAFETY_LIMIT",
        )

    async def _collect_pages(
        self,
        pages: AsyncIterator[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        async for page in pages:
            items.extend(page)
        return items

    @staticmethod
    def _modified_filter(field: str, modified_since: str | None) -> dict[str, str]:
        if not modified_since:
            return {}
        return {f">={field}": modified_since}

    async def iter_sync_deals(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._iter_id_cursor_pages(
            "crm.deal.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
                "filter": self._modified_filter("DATE_MODIFY", modified_since),
            },
            max_items=max_items,
        ):
            yield page

    async def list_sync_deals(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect_pages(
            self.iter_sync_deals(max_items=max_items, modified_since=modified_since)
        )

    async def iter_sync_leads(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._iter_id_cursor_pages(
            "crm.lead.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
                "filter": self._modified_filter("DATE_MODIFY", modified_since),
            },
            max_items=max_items,
        ):
            yield page

    async def list_sync_leads(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect_pages(
            self.iter_sync_leads(max_items=max_items, modified_since=modified_since)
        )

    async def iter_sync_contacts(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._iter_id_cursor_pages(
            "crm.contact.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
                "filter": self._modified_filter("DATE_MODIFY", modified_since),
            },
            max_items=max_items,
        ):
            yield page

    async def list_sync_contacts(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect_pages(
            self.iter_sync_contacts(max_items=max_items, modified_since=modified_since)
        )

    async def iter_sync_companies(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._iter_id_cursor_pages(
            "crm.company.list",
            {
                "select": ["*", "UF_*"],
                "order": {"ID": "ASC"},
                "filter": self._modified_filter("DATE_MODIFY", modified_since),
            },
            max_items=max_items,
        ):
            yield page

    async def list_sync_companies(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect_pages(
            self.iter_sync_companies(max_items=max_items, modified_since=modified_since)
        )

    async def iter_sync_activities(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._iter_id_cursor_pages(
            "crm.activity.list",
            {
                "select": ["*"],
                "order": {"ID": "ASC"},
                "filter": self._modified_filter("LAST_UPDATED", modified_since),
            },
            max_items=max_items,
        ):
            yield page

    async def list_sync_activities(
        self,
        *,
        max_items: int | None = None,
        modified_since: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect_pages(
            self.iter_sync_activities(max_items=max_items, modified_since=modified_since)
        )

    async def iter_sync_stage_history(
        self,
        *,
        entity_type_id: int,
        max_items: int | None = None,
        created_since: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._iter_id_cursor_pages(
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
                "filter": self._modified_filter("CREATED_TIME", created_since),
            },
            result_key="items",
            max_items=max_items,
        ):
            yield page

    async def list_sync_stage_history(
        self,
        *,
        entity_type_id: int,
        max_items: int | None = None,
        created_since: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect_pages(
            self.iter_sync_stage_history(
                entity_type_id=entity_type_id,
                max_items=max_items,
                created_since=created_since,
            )
        )
