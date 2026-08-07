from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from app.config import Settings
from app.integrations.bitrix24 import Bitrix24RequestError
from app.integrations.bitrix24.inventory_client import InventoryBitrix24Client
from app.proxy import build_proxy_url


@dataclass(frozen=True, slots=True)
class InventoryTarget:
    group: str
    label: str
    method: str


@dataclass(frozen=True, slots=True)
class MethodCapability:
    target: InventoryTarget
    exists: bool | None
    available: bool


@dataclass(frozen=True, slots=True)
class FieldCapability:
    label: str
    method: str
    total: int
    custom: int
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class Bitrix24Inventory:
    source: str
    methods: tuple[MethodCapability, ...]
    fields: tuple[FieldCapability, ...]


INVENTORY_TARGETS: Final[tuple[InventoryTarget, ...]] = (
    InventoryTarget("Система", "Проверка методов REST", "method.get"),
    InventoryTarget("Сотрудники", "Пользователи", "user.get"),
    InventoryTarget("Сотрудники", "Поля пользователей", "user.fields"),
    InventoryTarget("Сотрудники", "Подразделения", "department.get"),
    InventoryTarget("CRM", "Сделки", "crm.deal.list"),
    InventoryTarget("CRM", "Поля сделок", "crm.deal.fields"),
    InventoryTarget("CRM", "История движения по стадиям", "crm.stagehistory.list"),
    InventoryTarget("CRM", "Лиды", "crm.lead.list"),
    InventoryTarget("CRM", "Поля лидов", "crm.lead.fields"),
    InventoryTarget("CRM", "Контакты", "crm.contact.list"),
    InventoryTarget("CRM", "Поля контактов", "crm.contact.fields"),
    InventoryTarget("CRM", "Компании", "crm.company.list"),
    InventoryTarget("CRM", "Поля компаний", "crm.company.fields"),
    InventoryTarget("CRM", "Активности CRM", "crm.activity.list"),
    InventoryTarget("CRM", "Поля активностей", "crm.activity.fields"),
    InventoryTarget("CRM", "Комментарии таймлайна", "crm.timeline.comment.list"),
    InventoryTarget(
        "Коммуникации",
        "Расшифровки звонков",
        "crm.activity.call.gettranscript",
    ),
    InventoryTarget(
        "Коммуникации",
        "История Открытых линий",
        "imopenlines.session.history.get",
    ),
    InventoryTarget(
        "Коммуникации",
        "CRM-чаты Открытых линий",
        "imopenlines.crm.chat.get",
    ),
    InventoryTarget("Задачи", "Задачи сотрудников", "tasks.task.list"),
    InventoryTarget(
        "Товары",
        "Товарные позиции CRM",
        "crm.item.productrow.list",
    ),
    InventoryTarget("Смарт-процессы", "Типы смарт-процессов", "crm.type.list"),
    InventoryTarget("Смарт-процессы", "Элементы универсального CRM", "crm.item.list"),
    InventoryTarget("Телефония", "Статистика телефонии", "voximplant.statistic.get"),
    InventoryTarget("Файлы", "Файлы Диска", "disk.file.get"),
)

FIELD_TARGETS: Final[tuple[tuple[str, str], ...]] = (
    ("Поля сделок", "crm.deal.fields"),
    ("Поля лидов", "crm.lead.fields"),
)


def build_inventory_client(settings: Settings) -> InventoryBitrix24Client:
    return InventoryBitrix24Client(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
    )


def _field_names(result: Any) -> tuple[str, ...]:
    if isinstance(result, dict):
        return tuple(str(name) for name in result)
    if isinstance(result, list):
        names: list[str] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            raw_name = item.get("ID") or item.get("id") or item.get("NAME")
            if raw_name:
                names.append(str(raw_name))
        return tuple(names)
    return ()


async def inspect_bitrix24(
    client: InventoryBitrix24Client,
) -> Bitrix24Inventory:
    capabilities: list[MethodCapability] = []
    source = "method.get"

    try:
        for target in INVENTORY_TARGETS:
            exists, available = await client.method_status(target.method)
            capabilities.append(
                MethodCapability(
                    target=target,
                    exists=exists,
                    available=available,
                )
            )
    except Bitrix24RequestError:
        available_methods = await client.available_methods()
        source = "methods"
        capabilities = [
            MethodCapability(
                target=target,
                exists=None,
                available=target.method.lower() in available_methods,
            )
            for target in INVENTORY_TARGETS
        ]

    available_by_method = {
        capability.target.method: capability.available
        for capability in capabilities
    }
    fields: list[FieldCapability] = []
    for label, method in FIELD_TARGETS:
        if not available_by_method.get(method, False):
            continue
        try:
            response = await client.call(method)
            names = _field_names(response.get("result"))
            custom_count = sum(name.upper().startswith("UF_") for name in names)
            fields.append(
                FieldCapability(
                    label=label,
                    method=method,
                    total=len(names),
                    custom=custom_count,
                )
            )
        except Bitrix24RequestError as exc:
            fields.append(
                FieldCapability(
                    label=label,
                    method=method,
                    total=0,
                    custom=0,
                    error_code=exc.error_code or "UNKNOWN",
                )
            )

    return Bitrix24Inventory(
        source=source,
        methods=tuple(capabilities),
        fields=tuple(fields),
    )


async def fetch_bitrix24_inventory(settings: Settings) -> Bitrix24Inventory:
    return await inspect_bitrix24(build_inventory_client(settings))


def format_bitrix24_inventory(inventory: Bitrix24Inventory) -> str:
    available = sum(item.available for item in inventory.methods)
    restricted = sum(
        item.exists is True and not item.available
        for item in inventory.methods
    )
    absent = sum(item.exists is False for item in inventory.methods)

    lines = [
        "Инвентаризация Bitrix24 для ИИ-РОПа",
        f"Источник проверки: {inventory.source}",
        f"Доступно методов: {available}/{len(inventory.methods)}",
    ]
    if inventory.source == "method.get":
        lines.append(f"Закрыто правами: {restricted}")
        lines.append(f"Отсутствует на портале: {absent}")
    else:
        lines.append(
            "Старая коробка: отсутствующие и закрытые правами методы "
            "нельзя различить автоматически."
        )

    current_group: str | None = None
    for capability in inventory.methods:
        target = capability.target
        if target.group != current_group:
            current_group = target.group
            lines.append(f"\n{current_group}:")

        if capability.available:
            marker = "✅"
            status = "доступен"
        elif capability.exists is True:
            marker = "⚠️"
            status = "есть, но закрыт правами вебхука"
        elif capability.exists is False:
            marker = "❌"
            status = "метода нет на этой коробке"
        else:
            marker = "⚪"
            status = "не доступен текущему вебхуку"

        lines.append(
            f"{marker} {target.label} — {status}\n"
            f"   {target.method}"
        )

    if inventory.fields:
        lines.append("\nСхема CRM:")
        for field in inventory.fields:
            if field.error_code:
                lines.append(
                    f"⚠️ {field.label}: не удалось прочитать "
                    f"({field.error_code})"
                )
                continue
            standard = field.total - field.custom
            lines.append(
                f"• {field.label}: всего {field.total}, "
                f"стандартных {standard}, пользовательских {field.custom}"
            )

    lines.append(
        "\nПроверка выполнялась только read-only методами. "
        "Данные CRM в OpenAI не передавались."
    )
    return "\n".join(lines)
