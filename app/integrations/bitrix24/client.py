from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

BITRIX24_READ_ONLY_METHODS: Final[frozenset[str]] = frozenset(
    {
        "profile",
        "user.current",
        "user.get",
        "crm.category.list",
        "crm.status.list",
        "crm.deal.list",
        "crm.deal.fields",
        "crm.stagehistory.list",
        "crm.lead.list",
        "crm.lead.fields",
    }
)

DEAL_ANALYTICS_FIELDS: Final[tuple[str, ...]] = (
    "ID",
    "CATEGORY_ID",
    "STAGE_ID",
    "STAGE_SEMANTIC_ID",
    "OPPORTUNITY",
    "CURRENCY_ID",
    "ASSIGNED_BY_ID",
    "DATE_CREATE",
    "DATE_MODIFY",
    "MOVED_TIME",
    "CLOSEDATE",
    "CLOSED",
    "SOURCE_ID",
)

LEAD_DEMO_FIELDS: Final[tuple[str, ...]] = (
    "ID",
    "TITLE",
    "STATUS_ID",
    "STATUS_SEMANTIC_ID",
    "SOURCE_ID",
    "ASSIGNED_BY_ID",
    "DATE_CREATE",
    "DATE_MODIFY",
    "OPPORTUNITY",
    "CURRENCY_ID",
)

USER_DIRECTORY_FIELDS: Final[tuple[str, ...]] = (
    "ID",
    "NAME",
    "LAST_NAME",
    "ACTIVE",
)


class Bitrix24ConfigurationError(ValueError):
    """Raised when the Bitrix24 connection settings are unsafe or incomplete."""


class Bitrix24ReadOnlyViolation(PermissionError):
    """Raised when code attempts to call a method outside the read-only allowlist."""


class Bitrix24RequestError(RuntimeError):
    """A safe error that never includes the webhook secret or response body."""

    def __init__(self, public_message: str, *, error_code: str | None = None) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.error_code = error_code


def normalize_webhook_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        raise Bitrix24ConfigurationError("BITRIX24_WEBHOOK_URL is not configured")

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise Bitrix24ConfigurationError("Bitrix24 webhook URL must use HTTPS")
    if not parsed.hostname:
        raise Bitrix24ConfigurationError("Bitrix24 webhook URL has no hostname")
    if parsed.username or parsed.password:
        raise Bitrix24ConfigurationError("Credentials in the Bitrix24 hostname are forbidden")
    if parsed.query or parsed.fragment:
        raise Bitrix24ConfigurationError("Bitrix24 webhook URL must not contain query or fragment")

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 3 or path_parts[0].lower() != "rest":
        raise Bitrix24ConfigurationError(
            "Bitrix24 webhook URL must end with /rest/<user_id>/<secret>/"
        )

    user_id, secret = path_parts[1], path_parts[2]
    if not user_id.isdigit():
        raise Bitrix24ConfigurationError("Bitrix24 webhook user ID must be numeric")
    if len(secret) < 8:
        raise Bitrix24ConfigurationError("Bitrix24 webhook secret is invalid")

    normalized_path = f"/rest/{user_id}/{secret}/"
    return urlunsplit(("https", parsed.netloc, normalized_path, "", ""))


class Bitrix24ReadOnlyClient:
    def __init__(
        self,
        webhook_url: str,
        *,
        timeout_seconds: float = 15.0,
        verify_ssl: bool = True,
        max_pages: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._webhook_url = normalize_webhook_url(webhook_url)
        self._timeout = httpx.Timeout(timeout_seconds)
        self._verify_ssl = verify_ssl
        self._max_pages = max_pages
        self._transport = transport

    @property
    def portal_host(self) -> str:
        return urlsplit(self._webhook_url).hostname or "unknown"

    def _endpoint(self, method: str) -> str:
        if method not in BITRIX24_READ_ONLY_METHODS:
            raise Bitrix24ReadOnlyViolation(
                f"Bitrix24 method is not permitted in read-only mode: {method}"
            )
        return f"{self._webhook_url}{method}.json"

    async def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        endpoint = self._endpoint(method)
        payload = dict(params or {})
        if "auth" in payload:
            raise Bitrix24ReadOnlyViolation("Auth tokens must not be passed in request payloads")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                verify=self._verify_ssl,
                follow_redirects=False,
                transport=self._transport,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Agency-Stack/0.3",
                },
            ) as client:
                response = await client.post(endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise Bitrix24RequestError("Bitrix24 request timed out") from exc
        except httpx.RequestError as exc:
            raise Bitrix24RequestError("Unable to connect to Bitrix24") from exc

        if not 200 <= response.status_code < 300:
            logger.warning(
                "Bitrix24 HTTP error",
                extra={
                    "event": "bitrix24_http_error",
                    "method": method,
                    "status_code": response.status_code,
                },
            )
            raise Bitrix24RequestError(
                "Bitrix24 returned an HTTP error",
                error_code=f"HTTP_{response.status_code}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise Bitrix24RequestError("Bitrix24 returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise Bitrix24RequestError("Bitrix24 returned an unexpected response")

        error_code = data.get("error")
        if error_code:
            safe_code = str(error_code)[:80]
            logger.warning(
                "Bitrix24 API error",
                extra={
                    "event": "bitrix24_api_error",
                    "method": method,
                    "error_code": safe_code,
                },
            )
            raise Bitrix24RequestError(
                f"Bitrix24 rejected the request: {safe_code}",
                error_code=safe_code,
            )

        return data

    async def call_all(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        result_key: str | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_start = 0

        for _page_number in range(self._max_pages):
            payload = dict(params or {})
            payload["start"] = next_start
            response = await self.call(method, payload)
            result = response.get("result")

            if result_key is not None:
                if not isinstance(result, dict):
                    raise Bitrix24RequestError("Bitrix24 returned an invalid list container")
                page_items = result.get(result_key, [])
            else:
                page_items = result

            if not isinstance(page_items, list):
                raise Bitrix24RequestError("Bitrix24 returned an invalid list response")

            for item in page_items:
                if isinstance(item, dict):
                    items.append(item)
                    if max_items is not None and len(items) >= max_items:
                        return items[:max_items]

            raw_next = response.get("next")
            if raw_next is None:
                return items

            try:
                next_start = int(raw_next)
            except (TypeError, ValueError) as exc:
                raise Bitrix24RequestError("Bitrix24 returned invalid pagination data") from exc

        raise Bitrix24RequestError("Bitrix24 pagination safety limit was reached")

    async def profile(self) -> dict[str, Any]:
        response = await self.call("profile")
        result = response.get("result")
        if not isinstance(result, dict):
            raise Bitrix24RequestError("Bitrix24 returned an invalid profile")
        return result

    async def list_users(self, *, max_items: int = 500) -> list[dict[str, Any]]:
        return await self.call_all(
            "user.get",
            {
                "filter": {"ACTIVE": "Y"},
                "select": list(USER_DIRECTORY_FIELDS),
            },
            max_items=max_items,
        )

    async def list_deal_categories(self) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.category.list",
            {"entityTypeId": 2},
            result_key="categories",
        )

    async def list_deal_stages(self, category_id: int) -> list[dict[str, Any]]:
        entity_id = "DEAL_STAGE" if category_id == 0 else f"DEAL_STAGE_{category_id}"
        return await self.call_all(
            "crm.status.list",
            {
                "order": {"SORT": "ASC"},
                "filter": {"ENTITY_ID": entity_id},
            },
        )

    async def list_deals(
        self,
        *,
        max_items: int = 200,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {}
        if modified_after:
            filters[">DATE_MODIFY"] = modified_after

        return await self.call_all(
            "crm.deal.list",
            {
                "select": list(DEAL_ANALYTICS_FIELDS),
                "filter": filters,
                "order": {"ID": "DESC"},
            },
            max_items=max_items,
        )

    async def list_leads(self, *, max_items: int = 200) -> list[dict[str, Any]]:
        return await self.call_all(
            "crm.lead.list",
            {
                "select": list(LEAD_DEMO_FIELDS),
                "order": {"ID": "DESC"},
            },
            max_items=max_items,
        )
