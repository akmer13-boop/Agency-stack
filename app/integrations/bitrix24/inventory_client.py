from __future__ import annotations

from typing import Any

from app.integrations.bitrix24.client import (
    Bitrix24ReadOnlyClient,
    Bitrix24RequestError,
)


INVENTORY_SYSTEM_METHODS = frozenset({"method.get", "methods"})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "y", "yes"}


class InventoryBitrix24Client(Bitrix24ReadOnlyClient):
    """Read-only client for discovering portal capabilities."""

    def _endpoint(self, method: str) -> str:
        if method in INVENTORY_SYSTEM_METHODS:
            return f"{self._webhook_url}{method}.json"
        return super()._endpoint(method)

    async def method_status(self, method_name: str) -> tuple[bool, bool]:
        response = await self.call(
            "method.get",
            {"name": method_name.strip().lower()},
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise Bitrix24RequestError(
                "Bitrix24 returned an invalid method.get response",
                error_code="INVALID_METHOD_STATUS",
            )
        return (
            _as_bool(result.get("isExisting")),
            _as_bool(result.get("isAvailable")),
        )

    async def available_methods(self) -> frozenset[str]:
        response = await self.call("methods")
        result = response.get("result")
        if not isinstance(result, list):
            raise Bitrix24RequestError(
                "Bitrix24 returned an invalid methods response",
                error_code="INVALID_METHOD_LIST",
            )
        return frozenset(str(item).strip().lower() for item in result if item)
