from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import Settings
from app.services.bitrix24_service import build_bitrix24_client


@dataclass(frozen=True, slots=True)
class PipelineStageGroup:
    category_id: int
    category_name: str
    stages: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class DealSummary:
    total: int
    active: int
    won: int
    lost: int
    without_responsible: int
    by_category: tuple[tuple[str, int], ...]
    by_stage: tuple[tuple[str, int], ...]
    opportunity_by_currency: tuple[tuple[str, Decimal], ...]


def _value(item: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def _to_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in {"1", "Y", "YES", "TRUE"}


def _format_decimal(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"))
    return f"{quantized:,.2f}".replace(",", " ")


async def fetch_deal_categories(settings: Settings) -> list[dict[str, Any]]:
    client = build_bitrix24_client(settings)
    return await client.list_deal_categories()


async def fetch_pipeline_stages(settings: Settings) -> tuple[PipelineStageGroup, ...]:
    client = build_bitrix24_client(settings)
    categories = await client.list_deal_categories()
    groups: list[PipelineStageGroup] = []

    for category in categories:
        category_id = _to_int(_value(category, "id", "ID"))
        category_name = str(
            _value(
                category,
                "name",
                "NAME",
                default=f"Воронка {category_id}",
            )
        )
        stages = await client.list_deal_stages(category_id)
        groups.append(
            PipelineStageGroup(
                category_id=category_id,
                category_name=category_name,
                stages=tuple(stages),
            )
        )

    return tuple(groups)


async def fetch_recent_deals(
    settings: Settings,
    *,
    max_items: int,
) -> list[dict[str, Any]]:
    client = build_bitrix24_client(settings)
    return await client.list_deals(max_items=max_items)


async def fetch_deal_summary(
    settings: Settings,
    *,
    max_items: int,
) -> DealSummary:
    deals = await fetch_recent_deals(settings, max_items=max_items)
    return build_deal_summary(deals)


def build_deal_summary(deals: list[dict[str, Any]]) -> DealSummary:
    active = 0
    won = 0
    lost = 0
    without_responsible = 0
    by_category: Counter[str] = Counter()
    by_stage: Counter[str] = Counter()
    opportunity_by_currency: defaultdict[str, Decimal] = defaultdict(
        lambda: Decimal("0")
    )

    for deal in deals:
        semantic = str(
            _value(deal, "STAGE_SEMANTIC_ID", "stageSemanticId", default="P")
        ).upper()
        if semantic == "S":
            won += 1
        elif semantic == "F":
            lost += 1
        else:
            active += 1

        category = str(_value(deal, "CATEGORY_ID", "categoryId", default="0"))
        stage = str(_value(deal, "STAGE_ID", "stageId", default="не указана"))
        by_category[category] += 1
        by_stage[stage] += 1

        responsible = _value(deal, "ASSIGNED_BY_ID", "assignedById")
        if responsible in (None, "", "0", 0):
            without_responsible += 1

        currency = str(
            _value(deal, "CURRENCY_ID", "currencyId", default="без валюты")
        ).upper()
        opportunity_by_currency[currency] += _to_decimal(
            _value(deal, "OPPORTUNITY", "opportunity")
        )

    return DealSummary(
        total=len(deals),
        active=active,
        won=won,
        lost=lost,
        without_responsible=without_responsible,
        by_category=tuple(by_category.most_common()),
        by_stage=tuple(by_stage.most_common()),
        opportunity_by_currency=tuple(sorted(opportunity_by_currency.items())),
    )


def format_deal_categories(categories: list[dict[str, Any]]) -> str:
    if not categories:
        return "Воронки Bitrix24 не найдены."

    normalized = sorted(
        categories,
        key=lambda item: _to_int(_value(item, "sort", "SORT", "id", "ID")),
    )
    lines = [f"Воронки Bitrix24: {len(normalized)}"]
    for category in normalized:
        category_id = _value(category, "id", "ID", default="?")
        name = _value(category, "name", "NAME", default=f"Воронка {category_id}")
        default_label = (
            " — основная"
            if _is_true(_value(category, "isDefault", "IS_DEFAULT"))
            else ""
        )
        lines.append(f"• {name} (ID {category_id}){default_label}")
    return "\n".join(lines)


def format_pipeline_stages(groups: tuple[PipelineStageGroup, ...]) -> str:
    if not groups:
        return "Стадии воронок Bitrix24 не найдены."

    lines = ["Стадии воронок Bitrix24:"]
    for group in groups:
        lines.append(f"\n{group.category_name} (ID {group.category_id})")
        if not group.stages:
            lines.append("• стадии отсутствуют")
            continue

        stages = sorted(
            group.stages,
            key=lambda item: _to_int(_value(item, "SORT", "sort")),
        )
        for stage in stages:
            stage_id = _value(stage, "STATUS_ID", "statusId", default="?")
            name = _value(stage, "NAME", "name", default=stage_id)
            semantic = _value(stage, "SEMANTICS", "semantics", default="")
            semantic_label = f" [{semantic}]" if semantic else ""
            lines.append(f"• {name} — {stage_id}{semantic_label}")
    return "\n".join(lines)


def format_recent_deals(deals: list[dict[str, Any]]) -> str:
    if not deals:
        return "Сделки Bitrix24 не найдены."

    lines = [f"Последние тестовые сделки: {len(deals)}"]
    for deal in deals:
        deal_id = _value(deal, "ID", "id", default="?")
        category = _value(deal, "CATEGORY_ID", "categoryId", default="0")
        stage = _value(deal, "STAGE_ID", "stageId", default="не указана")
        amount = _to_decimal(_value(deal, "OPPORTUNITY", "opportunity"))
        currency = str(
            _value(deal, "CURRENCY_ID", "currencyId", default="")
        ).upper()
        responsible = _value(
            deal,
            "ASSIGNED_BY_ID",
            "assignedById",
            default="не назначен",
        )
        modified = _value(
            deal,
            "DATE_MODIFY",
            "dateModify",
            default="нет даты",
        )

        amount_text = _format_decimal(amount)
        if currency:
            amount_text = f"{amount_text} {currency}"

        lines.append(
            f"• #{deal_id} | воронка {category} | стадия {stage}\n"
            f"  сумма: {amount_text} | ответственный: {responsible}\n"
            f"  изменена: {modified}"
        )
    return "\n".join(lines)


def format_deal_summary(summary: DealSummary) -> str:
    if summary.total == 0:
        return "Для сводки сделки Bitrix24 не найдены."

    lines = [
        "Локальная сводка Bitrix24:",
        f"• обработано сделок: {summary.total}",
        f"• активные: {summary.active}",
        f"• успешные: {summary.won}",
        f"• проигранные: {summary.lost}",
        f"• без ответственного: {summary.without_responsible}",
    ]

    if summary.opportunity_by_currency:
        lines.append("\nСумма сделок по валютам:")
        for currency, amount in summary.opportunity_by_currency:
            lines.append(f"• {currency}: {_format_decimal(amount)}")

    if summary.by_category:
        lines.append("\nПо воронкам:")
        for category, count in summary.by_category:
            lines.append(f"• ID {category}: {count}")

    if summary.by_stage:
        lines.append("\nТоп стадий:")
        for stage, count in summary.by_stage[:10]:
            lines.append(f"• {stage}: {count}")

    lines.append("\nСводка рассчитана локально. Данные сделок в OpenAI не передавались.")
    return "\n".join(lines)
