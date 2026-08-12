from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import aiosqlite

from app.config import Settings
from app.integrations.bitrix24.client import Bitrix24ReadOnlyClient
from app.proxy import build_proxy_url
from app.storage.crm_store import CrmStore


@dataclass(frozen=True, slots=True)
class EmployeeIdentity:
    user_id: str
    display_name: str
    active: bool
    department_ids: tuple[str, ...]
    department_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RopDirectory:
    users: dict[str, EmployeeIdentity]
    departments: dict[str, str]


@dataclass(frozen=True, slots=True)
class DirectorySyncResult:
    users: int
    departments: int


def build_directory_client(settings: Settings) -> Bitrix24ReadOnlyClient:
    return Bitrix24ReadOnlyClient(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
    )


async def sync_rop_directory(settings: Settings) -> DirectorySyncResult:
    client = build_directory_client(settings)
    users = await client.list_users(max_items=1000)
    departments = await client.list_departments()

    store = CrmStore(settings.database_path)
    await store.initialize()
    user_count = await store.upsert_entities("user", users)
    department_count = await store.upsert_entities("department", departments)
    return DirectorySyncResult(users=user_count, departments=department_count)


async def _load_payloads(database_path: str, entity_type: str) -> list[dict[str, Any]]:
    store = CrmStore(database_path)
    await store.initialize()
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT payload_json
            FROM crm_active_entities
            WHERE entity_type = ?
            ORDER BY CAST(entity_id AS INTEGER)
            """,
            (entity_type,),
        )
        rows = await cursor.fetchall()

    result: list[dict[str, Any]] = []
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            result.append(payload)
    return result


def _department_ids(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


def _display_name(user: dict[str, Any]) -> str:
    parts = [
        str(user.get("NAME") or "").strip(),
        str(user.get("LAST_NAME") or "").strip(),
    ]
    value = " ".join(part for part in parts if part).strip()
    if value:
        return value
    return f"ID {user.get('ID', '?')}"


async def load_rop_directory(database_path: str) -> RopDirectory:
    departments_payload = await _load_payloads(database_path, "department")
    departments = {
        str(item.get("ID")): str(item.get("NAME") or f"Отдел {item.get('ID')}").strip()
        for item in departments_payload
        if item.get("ID") is not None
    }

    users: dict[str, EmployeeIdentity] = {}
    for item in await _load_payloads(database_path, "user"):
        raw_id = item.get("ID")
        if raw_id is None:
            continue
        user_id = str(raw_id)
        department_ids = _department_ids(item.get("UF_DEPARTMENT"))
        department_names = tuple(
            departments[department_id]
            for department_id in department_ids
            if department_id in departments
        )
        users[user_id] = EmployeeIdentity(
            user_id=user_id,
            display_name=_display_name(item),
            active=bool(item.get("ACTIVE", True)),
            department_ids=department_ids,
            department_names=department_names,
        )

    return RopDirectory(users=users, departments=departments)


def employee_label(directory: RopDirectory, user_id: str, *, include_id: bool = True) -> str:
    identity = directory.users.get(str(user_id))
    if identity is None:
        return f"ID {user_id}"

    department = " / ".join(identity.department_names)
    label = identity.display_name
    if department:
        label = f"{label} · {department}"
    if include_id:
        label = f"{label} (ID {identity.user_id})"
    return label


def enrich_responsible_ids(text: str, directory: RopDirectory) -> str:
    """Replace manager IDs in deterministic Telegram reports with local directory labels."""

    def replace_responsible(match: re.Match[str]) -> str:
        user_id = match.group(1)
        return f"отв. {employee_label(directory, user_id)}"

    def replace_bullet_pipe(match: re.Match[str]) -> str:
        user_id = match.group(1)
        return f"• {employee_label(directory, user_id)} |"

    def replace_bullet_colon(match: re.Match[str]) -> str:
        user_id = match.group(1)
        return f"• {employee_label(directory, user_id)}:"

    value = re.sub(r"отв\. ID ([^ |]+)", replace_responsible, text)
    value = re.sub(r"• ID ([^ |:]+) \|", replace_bullet_pipe, value)
    value = re.sub(r"• ID ([^ |:]+):", replace_bullet_colon, value)
    return value


def format_directory_sync_result(result: DirectorySyncResult) -> str:
    return (
        "Справочник сотрудников Bitrix24 обновлён.\n"
        f"• Сотрудники: {result.users}\n"
        f"• Подразделения: {result.departments}\n"
        "Сохранены только поля, нужные ИИ-РОПу: ID, ФИО, активность, должность и "
        "привязка к подразделению. Запись в Bitrix24 не выполнялась."
    )
