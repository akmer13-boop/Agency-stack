from __future__ import annotations

from typing import Any

from app.integrations.bitrix24.client import (
    DEAL_ANALYTICS_FIELDS,
    Bitrix24ReadOnlyClient,
    Bitrix24RequestError,
)


def _translate_deal_error(error: Bitrix24RequestError) -> Bitrix24RequestError:
    code = error.error_code or "UNKNOWN"
    if code == "HTTP_400":
        message = (
            "Bitrix24 отклонил параметры чтения сделок (HTTP 400). "
            "Клиент уже отфильтровал неподдерживаемые поля; проверьте версию CRM и права вебхука."
        )
    elif code == "HTTP_403" or code.upper() in {"ACCESS_DENIED", "NO_ACCESS"}:
        message = (
            "Bitrix24 запретил чтение сделок. Дайте пользователю вебхука право просмотра CRM-сделок."
        )
    else:
        message = f"Bitrix24 не смог вернуть сделки ({code})."
    return Bitrix24RequestError(message, error_code=code)


class CompatibleBitrix24ReadOnlyClient(Bitrix24ReadOnlyClient):
    """Read-only client that adapts deal fields to the installed boxed version."""

    async def _supported_deal_fields(self) -> tuple[str, ...]:
        try:
            response = await self.call("crm.deal.fields")
        except Bitrix24RequestError as exc:
            raise _translate_deal_error(exc) from exc

        result = response.get("result")
        if not isinstance(result, dict):
            raise Bitrix24RequestError(
                "Bitrix24 вернул некорректный список полей сделок",
                error_code="INVALID_DEAL_FIELDS",
            )

        available = {str(field_name).upper() for field_name in result}
        supported = tuple(
            field_name
            for field_name in DEAL_ANALYTICS_FIELDS
            if field_name.upper() in available
        )
        if "ID" not in supported:
            raise Bitrix24RequestError(
                "Bitrix24 не сообщил обязательное поле ID сделки",
                error_code="DEAL_ID_FIELD_MISSING",
            )
        return supported

    async def list_deals(
        self,
        *,
        max_items: int = 200,
        modified_after: str | None = None,
    ) -> list[dict[str, Any]]:
        supported_fields = await self._supported_deal_fields()
        params: dict[str, Any] = {
            "select": list(supported_fields),
            "order": {"ID": "DESC"},
        }
        if modified_after:
            params["filter"] = {">DATE_MODIFY": modified_after}

        try:
            return await self.call_all(
                "crm.deal.list",
                params,
                max_items=max_items,
            )
        except Bitrix24RequestError as exc:
            raise _translate_deal_error(exc) from exc
