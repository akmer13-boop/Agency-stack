from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import aiosqlite

from app.storage.crm_store import CrmStore


@dataclass(frozen=True, slots=True)
class CurrencyKpi:
    currency: str
    active_pipeline: Decimal
    won_revenue: Decimal
    won_count: int

    @property
    def average_won_check(self) -> Decimal:
        if self.won_count <= 0:
            return Decimal("0")
        return self.won_revenue / self.won_count


@dataclass(frozen=True, slots=True)
class DealRisk:
    deal_id: str
    stage_id: str
    category_id: str
    assigned_by_id: str
    opportunity: Decimal
    currency: str
    idle_days: int
    last_movement_at: datetime | None


@dataclass(frozen=True, slots=True)
class RopSnapshot:
    generated_at: datetime
    deals_total: int
    active_deals: int
    won_deals: int
    lost_deals: int
    leads_total: int
    new_leads_24h: int
    new_deals_24h: int
    closed_conversion_percent: Decimal
    attention_3d: int
    critical_5d: int
    currencies: tuple[CurrencyKpi, ...]
    stage_counts: tuple[tuple[str, int], ...]
    category_counts: tuple[tuple[str, int], ...]
    risks: tuple[DealRisk, ...]


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _text(value: Any, default: str = "—") -> str:
    if value in (None, ""):
        return default
    return str(value)


def _semantic(item: dict[str, Any]) -> str:
    return _text(item.get("STAGE_SEMANTIC_ID"), "P").upper()


def _last_movement(item: dict[str, Any]) -> datetime | None:
    for key in ("MOVED_TIME", "DATE_MODIFY", "DATE_CREATE"):
        parsed = _datetime(item.get(key))
        if parsed is not None:
            return parsed
    return None


async def _load_raw_entities(database_path: str, entity_type: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT payload_json
            FROM crm_raw_entities
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


async def build_rop_snapshot(
    database_path: str,
    *,
    now: datetime | None = None,
    attention_days: int = 3,
    critical_days: int = 5,
    risk_limit: int = 20,
) -> RopSnapshot:
    store = CrmStore(database_path)
    await store.initialize()

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    deals = await _load_raw_entities(database_path, "deal")
    leads = await _load_raw_entities(database_path, "lead")

    active = 0
    won = 0
    lost = 0
    new_deals_24h = 0
    new_leads_24h = 0
    attention = 0
    critical = 0
    stage_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    pipeline_by_currency: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    won_by_currency: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    won_count_by_currency: Counter[str] = Counter()
    risks: list[DealRisk] = []
    cutoff_24h = reference - timedelta(hours=24)

    for lead in leads:
        created_at = _datetime(lead.get("DATE_CREATE"))
        if created_at is not None and created_at >= cutoff_24h:
            new_leads_24h += 1

    for deal in deals:
        semantic = _semantic(deal)
        stage_id = _text(deal.get("STAGE_ID"), "не указана")
        category_id = _text(deal.get("CATEGORY_ID"), "0")
        currency = _text(deal.get("CURRENCY_ID"), "N/A").upper()
        amount = _decimal(deal.get("OPPORTUNITY"))
        stage_counts[stage_id] += 1
        category_counts[category_id] += 1

        created_at = _datetime(deal.get("DATE_CREATE"))
        if created_at is not None and created_at >= cutoff_24h:
            new_deals_24h += 1

        if semantic == "S":
            won += 1
            won_by_currency[currency] += amount
            won_count_by_currency[currency] += 1
            continue
        if semantic == "F":
            lost += 1
            continue

        active += 1
        pipeline_by_currency[currency] += amount
        moved_at = _last_movement(deal)
        if moved_at is None:
            idle_days = max(attention_days, critical_days)
        else:
            idle_days = max(0, int((reference - moved_at).total_seconds() // 86400))

        if idle_days >= attention_days:
            attention += 1
        if idle_days >= critical_days:
            critical += 1

        if idle_days >= attention_days:
            risks.append(
                DealRisk(
                    deal_id=_text(deal.get("ID"), "?"),
                    stage_id=stage_id,
                    category_id=category_id,
                    assigned_by_id=_text(deal.get("ASSIGNED_BY_ID"), "не назначен"),
                    opportunity=amount,
                    currency=currency,
                    idle_days=idle_days,
                    last_movement_at=moved_at,
                )
            )

    closed = won + lost
    conversion = (
        (Decimal(won) / Decimal(closed) * Decimal("100"))
        if closed
        else Decimal("0")
    )

    currencies = tuple(
        CurrencyKpi(
            currency=currency,
            active_pipeline=pipeline_by_currency[currency],
            won_revenue=won_by_currency[currency],
            won_count=won_count_by_currency[currency],
        )
        for currency in sorted(set(pipeline_by_currency) | set(won_by_currency))
    )

    risks.sort(key=lambda item: (item.idle_days, item.opportunity), reverse=True)

    return RopSnapshot(
        generated_at=reference,
        deals_total=len(deals),
        active_deals=active,
        won_deals=won,
        lost_deals=lost,
        leads_total=len(leads),
        new_leads_24h=new_leads_24h,
        new_deals_24h=new_deals_24h,
        closed_conversion_percent=conversion,
        attention_3d=attention,
        critical_5d=critical,
        currencies=currencies,
        stage_counts=tuple(stage_counts.most_common()),
        category_counts=tuple(category_counts.most_common()),
        risks=tuple(risks[:risk_limit]),
    )


def _money(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"))
    return f"{quantized:,.2f}".replace(",", " ")


def _percent(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'))}%"


def format_rop_today(snapshot: RopSnapshot) -> str:
    lines = [
        "ИИ-РОП · сводка",
        f"• Сделок всего: {snapshot.deals_total}",
        f"• Активных: {snapshot.active_deals}",
        f"• Успешных: {snapshot.won_deals}",
        f"• Проигранных: {snapshot.lost_deals}",
        f"• Конверсия закрытых сделок в продажу: {_percent(snapshot.closed_conversion_percent)}",
        f"• Новых сделок за 24 ч: {snapshot.new_deals_24h}",
        f"• Новых лидов за 24 ч: {snapshot.new_leads_24h}",
        f"• Без движения ≥3 дней: {snapshot.attention_3d}",
        f"• Критические ≥5 дней: {snapshot.critical_5d}",
    ]

    if snapshot.currencies:
        lines.append("\nPipeline / успешные продажи:")
        for item in snapshot.currencies:
            lines.append(
                f"• {item.currency}: pipeline {_money(item.active_pipeline)} | "
                f"выиграно {_money(item.won_revenue)} | "
                f"средний чек {_money(item.average_won_check)}"
            )

    lines.append("\nРасчёт выполнен локально по SQLite. Raw CRM в OpenAI не передавалась.")
    return "\n".join(lines)


def format_rop_funnel(snapshot: RopSnapshot) -> str:
    lines = [
        "ИИ-РОП · воронка сделок",
        f"• Всего: {snapshot.deals_total}",
        f"• Активные: {snapshot.active_deals}",
        f"• Успешные: {snapshot.won_deals}",
        f"• Проигранные: {snapshot.lost_deals}",
        f"• Конверсия закрытых → продажа: {_percent(snapshot.closed_conversion_percent)}",
    ]

    if snapshot.category_counts:
        lines.append("\nПо воронкам (CATEGORY_ID):")
        for category_id, count in snapshot.category_counts[:15]:
            lines.append(f"• {category_id}: {count}")

    if snapshot.stage_counts:
        lines.append("\nТоп стадий:")
        for stage_id, count in snapshot.stage_counts[:20]:
            lines.append(f"• {stage_id}: {count}")

    lines.append("\nНазвания воронок/стадий добавим после бизнесовой сверки ID с Bitrix24.")
    return "\n".join(lines)


def format_rop_risks(snapshot: RopSnapshot) -> str:
    lines = [
        "ИИ-РОП · риски",
        f"• Без движения ≥3 дней: {snapshot.attention_3d}",
        f"• Критические ≥5 дней: {snapshot.critical_5d}",
    ]

    if not snapshot.risks:
        lines.append("\nЗависшие активные сделки не найдены.")
        return "\n".join(lines)

    lines.append("\nСамые долгие зависания:")
    for risk in snapshot.risks:
        amount = _money(risk.opportunity)
        lines.append(
            f"• #{risk.deal_id} | {risk.idle_days} дн. | стадия {risk.stage_id} | "
            f"{amount} {risk.currency} | ответственный ID {risk.assigned_by_id}"
        )

    lines.append("\nКритерий сейчас: последнее MOVED_TIME, затем DATE_MODIFY/DATE_CREATE.")
    return "\n".join(lines)
