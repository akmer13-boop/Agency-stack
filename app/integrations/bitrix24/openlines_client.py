from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.integrations.bitrix24.client import (
    Bitrix24ReadOnlyClient,
    Bitrix24ReadOnlyViolation,
    Bitrix24RequestError,
)

OPENLINES_READ_ONLY_METHODS = frozenset(
    {
        "im.dialog.messages.get",
        "imopenlines.crm.chat.get",
        "imopenlines.session.history.get",
    }
)


@dataclass(frozen=True, slots=True)
class CrmChatBatchItem:
    entity_type: str
    entity_id: str
    chats: tuple[dict[str, Any], ...]
    error_code: str | None = None


class OpenLinesReadOnlyClient(Bitrix24ReadOnlyClient):
    """Read-only client for CRM-linked Bitrix24 Open Lines conversations."""

    def _endpoint(self, method: str) -> str:
        if method in OPENLINES_READ_ONLY_METHODS:
            return f"{self._webhook_url}{method}.json"
        return super()._endpoint(method)

    async def get_crm_chats(
        self,
        entity_type: str,
        entity_id: str,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        normalized_type = entity_type.strip().lower()
        if normalized_type not in {"lead", "deal", "contact", "company"}:
            raise ValueError(f"Unsupported CRM entity type: {entity_type}")
        if not entity_id or not str(entity_id).isdigit():
            raise ValueError("CRM entity ID must be numeric")

        response = await self.call(
            "imopenlines.crm.chat.get",
            {
                "CRM_ENTITY_TYPE": normalized_type,
                "CRM_ENTITY": int(entity_id),
                "ACTIVE_ONLY": "Y" if active_only else "N",
            },
        )
        result = response.get("result")
        if not isinstance(result, list):
            raise Bitrix24RequestError("Bitrix24 returned an invalid Open Lines chat list")
        return [item for item in result if isinstance(item, dict)]

    async def get_session_history(self, chat_id: str) -> dict[str, Any]:
        if not chat_id or not str(chat_id).isdigit():
            raise ValueError("Open Lines chat ID must be numeric")

        response = await self.call(
            "imopenlines.session.history.get",
            {"CHAT_ID": int(chat_id)},
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise Bitrix24RequestError("Bitrix24 returned an invalid Open Lines history")
        return result

    async def get_dialog_messages(
        self,
        chat_id: str,
        *,
        last_id: int | None = None,
        first_id: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if not chat_id or not str(chat_id).isdigit():
            raise ValueError("Open Lines chat ID must be numeric")
        if last_id is not None and first_id is not None:
            raise ValueError("LAST_ID and FIRST_ID are mutually exclusive")
        if not 1 <= limit <= 50:
            raise ValueError("Bitrix24 dialog message LIMIT must be between 1 and 50")

        params: dict[str, Any] = {
            "DIALOG_ID": f"chat{int(chat_id)}",
            "LIMIT": limit,
        }
        if last_id is not None:
            if last_id < 1:
                raise ValueError("LAST_ID must be positive")
            params["LAST_ID"] = last_id
        if first_id is not None:
            if first_id < 1:
                raise ValueError("FIRST_ID must be positive")
            params["FIRST_ID"] = first_id

        response = await self.call("im.dialog.messages.get", params)
        result = response.get("result")
        if not isinstance(result, dict):
            raise Bitrix24RequestError("Bitrix24 returned an invalid dialog message history")
        messages = result.get("messages")
        if not isinstance(messages, list):
            raise Bitrix24RequestError("Bitrix24 returned an invalid dialog message list")
        return result

    async def get_crm_chats_batch(
        self,
        objects: list[tuple[str, str]],
    ) -> list[CrmChatBatchItem]:
        """Batch only the safe read-only imopenlines.crm.chat.get method."""
        if not objects:
            return []
        if len(objects) > 50:
            raise ValueError("Bitrix24 batch supports at most 50 commands")

        normalized: list[tuple[str, str]] = []
        for entity_type, entity_id in objects:
            normalized_type = entity_type.strip().lower()
            normalized_id = str(entity_id).strip()
            if normalized_type not in {"lead", "deal", "contact", "company"}:
                raise ValueError(f"Unsupported CRM entity type: {entity_type}")
            if not normalized_id.isdigit():
                raise ValueError("CRM entity ID must be numeric")
            normalized.append((normalized_type, normalized_id))

        commands: dict[str, str] = {}
        key_to_object: dict[str, tuple[str, str]] = {}
        for index, (entity_type, entity_id) in enumerate(normalized):
            key = f"q{index:02d}"
            query = urlencode(
                {
                    "CRM_ENTITY_TYPE": entity_type,
                    "CRM_ENTITY": int(entity_id),
                    "ACTIVE_ONLY": "N",
                }
            )
            commands[key] = f"imopenlines.crm.chat.get?{query}"
            key_to_object[key] = (entity_type, entity_id)

        endpoint = f"{self._webhook_url}batch.json"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                verify=self._verify_ssl,
                follow_redirects=False,
                proxy=self._proxy_url,
                transport=self._transport,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Agency-Stack-OpenLines-Batch/0.1",
                },
            ) as client:
                response = await client.post(
                    endpoint,
                    json={"halt": 0, "cmd": commands},
                )
        except httpx.TimeoutException as exc:
            raise Bitrix24RequestError("Bitrix24 batch request timed out") from exc
        except httpx.RequestError as exc:
            raise Bitrix24RequestError("Unable to connect to Bitrix24 batch endpoint") from exc

        if not 200 <= response.status_code < 300:
            raise Bitrix24RequestError(
                "Bitrix24 returned an HTTP error for Open Lines batch",
                error_code=f"HTTP_{response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise Bitrix24RequestError(
                "Bitrix24 returned invalid JSON for Open Lines batch"
            ) from exc

        if not isinstance(payload, dict):
            raise Bitrix24RequestError("Bitrix24 returned an unexpected Open Lines batch response")

        top_error = payload.get("error")
        if top_error:
            safe_code = str(top_error)[:80]
            raise Bitrix24RequestError(
                f"Bitrix24 rejected Open Lines batch: {safe_code}",
                error_code=safe_code,
            )

        outer = payload.get("result")
        if not isinstance(outer, dict):
            raise Bitrix24RequestError("Bitrix24 returned an invalid Open Lines batch container")

        result_map = outer.get("result")
        error_map = outer.get("result_error")
        result_map = result_map if isinstance(result_map, dict) else {}
        error_map = error_map if isinstance(error_map, dict) else {}

        items: list[CrmChatBatchItem] = []
        for key in commands:
            entity_type, entity_id = key_to_object[key]

            if key in error_map:
                raw_error = error_map[key]
                if isinstance(raw_error, dict):
                    error_code = str(raw_error.get("error") or "BATCH_ITEM_ERROR")[:80]
                else:
                    error_code = "BATCH_ITEM_ERROR"
                items.append(
                    CrmChatBatchItem(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        chats=(),
                        error_code=error_code,
                    )
                )
                continue

            raw_result = result_map.get(key)
            if not isinstance(raw_result, list):
                items.append(
                    CrmChatBatchItem(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        chats=(),
                        error_code="BATCH_ITEM_INVALID_RESULT",
                    )
                )
                continue

            chats = tuple(item for item in raw_result if isinstance(item, dict))
            items.append(
                CrmChatBatchItem(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    chats=chats,
                )
            )

        return items

    def assert_read_only(self, method: str) -> None:
        if method not in OPENLINES_READ_ONLY_METHODS:
            raise Bitrix24ReadOnlyViolation(
                f"Open Lines method is not permitted in read-only mode: {method}"
            )
