from __future__ import annotations

import logging
from typing import Any

from app.integrations.bitrix24.client import (
    DEAL_ANALYTICS_FIELDS,
    Bitrix24ReadOnlyClient,
    Bitrix24RequestError,
)

logger = logging.getLogger(__name__)


def _translate_deal_error(error: Bitrix24RequestError) -> Bitrix24RequestError:
    code = error.error_code or "UNKNOWN"
    if code == "HTTP_400":
        message = (
            "Bitrix24 отклонил параметры чтения сделок (HTTP 400). "
            "Клиент уже отфильтровал неподдерживаемые поля; проверьте версию CRM и права вебхука."
        )
    elif code == "HTTP_401":
        message = "Bitrix24 отклонил авторизацию вебхука при чтении сделок (HTTP 401)."
    elif code == "HTTP_403" or code.upper() in {"ACCESS_DENIED", "NO_ACCESS"}:
        message = (
            "Bitrix24 запретил чтение сделок. "
            "Дайте пользователю вебхука право просмотра CRM-сделок."
        )
    elif code == "HTTP_404":
        message = (
            "Метод чтения сделок недоступен в этой установке Bitrix24 (HTTP 404). "
            "Проверьте REST-модуль коробки и адрес вебхука."
        )
    else:
        message = f"Bitrix24 не смог вернуть сделки ({code})."
    return Bitrix24RequestError(message, error_code=code)


class CompatibleBitrix24ReadOnlyClient(Bitrix24ReadOnlyClient):
    """Read-only client that adapts requests to the installed boxed version."""

    async def list_users(self, *, max_items: int = 500) -> list[dict[str, Any]]:
        """Return an empty directory when the webhook cannot read users.

        User names are optional enrichment. Their absence must not block deal reports.
        """
        try:
            return await super().list_users(max_items=max_items)
        except Bitrix24RequestError as exc:
            logger.warning(
                "Bitrix24 user directory is unavailable; using numeric responsible IDs",
                extra={
                    "event": "bitrix24_user_directory_unavailable",
                    "error_code": exc.error_code or "UNKNOWN",
                },
            )
            return []

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
