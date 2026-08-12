from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from app.semantic.metrics import build_new_leads_metric
from app.semantic.models import SemanticLead
from app.semantic.repository import SemanticRepository
from app.services.rop_catalog import category_label, stage_label
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
class PeriodKpi:
    key: str
    label: str
    start_at: datetime
    end_at: datetime
    new_leads: int
    new_deals: int
    won_deals: int
    lost_deals: int
    conversion_percent: Decimal
    won_revenue_by_currency: tuple[tuple[str, Decimal], ...]


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
    timezone_name: str
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
    periods: tuple[PeriodKpi, ...] = ()

    def period(self, key: str) -> PeriodKpi | None:
        return next((item for item in self.periods if item.key == key), None)


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


def _closed_at(item: dict[str, Any]) -> datetime | None:
    for key in ("MOVED_TIME", "DATE_MODIFY"):
        parsed = _datetime(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _resolve_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _deal_in_scope(
    deal: dict[str, Any],
    *,
    included_category_ids: frozenset[str],
    excluded_stage_ids: frozenset[str],
) -> bool:
    category_id = _text(deal.get("CATEGORY_ID"), "0")
    stage_id = _text(deal.get("STAGE_ID"), "")
    if included_category_ids and category_id not in included_category_ids:
        return False
    return stage_id not in excluded_stage_ids


async def _load_raw_entities(database_path: str, entity_type: str) -> list[dict[str, Any]]:
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


def _build_period_kpi(
    key: str,
    label: str,
    *,
    start_at: datetime,
    end_at: datetime,
    deals: list[dict[str, Any]],
    leads: list[SemanticLead],
    timezone_name: str,
) -> PeriodKpi:
    new_leads = build_new_leads_metric(
        leads,
        period_start=start_at,
        period_end=end_at,
        timezone_name=timezone_name,
        calculated_at=end_at,
    ).value
    new_deals = 0
    won = 0
    lost = 0
    won_revenue: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for deal in deals:
        created_at = _datetime(deal.get("DATE_CREATE"))
        if created_at is not None and start_at <= created_at <= end_at:
            new_deals += 1

        semantic = _semantic(deal)
        if semantic not in {"S", "F"}:
            continue
        closed_at = _closed_at(deal)
        if closed_at is None or not start_at <= closed_at <= end_at:
            continue

        if semantic == "S":
            won += 1
            currency = _text(deal.get("CURRENCY_ID"), "N/A").upper()
            won_revenue[currency] += _decimal(deal.get("OPPORTUNITY"))
        else:
            lost += 1

    closed = won + lost
    conversion = Decimal(won) / Decimal(closed) * Decimal("100") if closed else Decimal("0")
    return PeriodKpi(
        key=key,
        label=label,
        start_at=start_at,
        end_at=end_at,
        new_leads=new_leads,
        new_deals=new_deals,
        won_deals=won,
        lost_deals=lost,
        conversion_percent=conversion,
        won_revenue_by_currency=tuple(sorted(won_revenue.items())),
    )


def _period_windows(
    reference: datetime,
    timezone_name: str,
) -> tuple[tuple[str, str, datetime], ...]:
    timezone = _resolve_timezone(timezone_name)
    local_now = reference.astimezone(timezone)
    local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_week_start = local_day_start - timedelta(days=6)
    local_month_start = local_day_start.replace(day=1)
    return (
        ("today", "Сегодня", local_day_start.astimezone(UTC)),
        ("week", "Последние 7 календарных дней", local_week_start.astimezone(UTC)),
        ("month", "Текущий месяц", local_month_start.astimezone(UTC)),
    )


async def build_rop_snapshot(
    database_path: str,
    *,
    now: datetime | None = None,
    attention_days: int = 3,
    critical_days: int = 5,
    risk_limit: int = 20,
    timezone_name: str = "Europe/Moscow",
    included_category_ids: frozenset[str] = frozenset(),
    excluded_stage_ids: frozenset[str] = frozenset(),
) -> RopSnapshot:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    raw_deals = await _load_raw_entities(database_path, "deal")
    leads = await SemanticRepository(database_path).leads()
    deals = [
        deal
        for deal in raw_deals
        if _deal_in_scope(
            deal,
            included_category_ids=included_category_ids,
            excluded_stage_ids=excluded_stage_ids,
        )
    ]

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

    new_leads_24h = build_new_leads_metric(
        leads,
        period_start=cutoff_24h,
        period_end=reference,
        timezone_name=timezone_name,
        calculated_at=reference,
    ).value

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
    conversion = Decimal(won) / Decimal(closed) * Decimal("100") if closed else Decimal("0")

    currencies = tuple(
        CurrencyKpi(
            currency=currency,
            active_pipeline=pipeline_by_currency[currency],
            won_revenue=won_by_currency[currency],
            won_count=won_count_by_currency[currency],
        )
        for currency in sorted(set(pipeline_by_currency) | set(won_by_currency))
    )

    periods = tuple(
        _build_period_kpi(
            key,
            label,
            start_at=start_at,
            end_at=reference,
            deals=deals,
            leads=leads,
            timezone_name=timezone_name,
        )
        for key, label, start_at in _period_windows(reference, timezone_name)
    )

    risks.sort(key=lambda item: (item.idle_days, item.opportunity), reverse=True)

    return RopSnapshot(
        generated_at=reference,
        timezone_name=timezone_name,
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
        periods=periods,
    )


def _money(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"))
    return f"{quantized:,.2f}".replace(",", " ")


def _percent(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'))}%"


def _format_period(snapshot: RopSnapshot, key: str) -> str:
    period = snapshot.period(key)
    if period is None:
        return "Период аналитики не рассчитан."

    lines = [
        f"ИИ-РОП · {period.label}",
        f"• Новых лидов: {period.new_leads}",
        f"• Новых сделок: {period.new_deals}",
        f"• Успешно закрыто: {period.won_deals}",
        f"• Проиграно: {period.lost_deals}",
        f"• Конверсия закрытых → продажа: {_percent(period.conversion_percent)}",
    ]
    if period.won_revenue_by_currency:
        lines.append("\nСумма успешных сделок за период:")
        for currency, amount in period.won_revenue_by_currency:
            lines.append(f"• {currency}: {_money(amount)}")
    else:
        lines.append("\nУспешных сделок с суммой в выбранном периоде нет.")

    lines.append(
        "\nПериод рассчитан локально. Для момента закрытия используется MOVED_TIME "
        "финальной стадии, с DATE_MODIFY как резервом."
    )
    return "\n".join(lines)


def format_rop_today(snapshot: RopSnapshot) -> str:
    period = snapshot.period("today")
    lines = [
        "ИИ-РОП · сегодня",
        f"• Активных сделок сейчас: {snapshot.active_deals}",
        f"• Без движения ≥3 дней: {snapshot.attention_3d}",
        f"• Критические ≥5 дней: {snapshot.critical_5d}",
    ]
    if period is not None:
        lines.extend(
            [
                f"• Новых лидов сегодня: {period.new_leads}",
                f"• Новых сделок сегодня: {period.new_deals}",
                f"• Успешно закрыто сегодня: {period.won_deals}",
                f"• Проиграно сегодня: {period.lost_deals}",
                f"• Конверсия закрытых сегодня: {_percent(period.conversion_percent)}",
            ]
        )
        if period.won_revenue_by_currency:
            lines.append("\nУспешные сделки сегодня:")
            for currency, amount in period.won_revenue_by_currency:
                lines.append(f"• {currency}: {_money(amount)}")

    if snapshot.currencies:
        lines.append("\nТекущий активный pipeline:")
        for item in snapshot.currencies:
            if item.active_pipeline:
                lines.append(f"• {item.currency}: {_money(item.active_pipeline)}")

    lines.append("\nРасчёт выполнен локально по SQLite. Raw CRM в OpenAI не передавалась.")
    return "\n".join(lines)


def format_rop_week(snapshot: RopSnapshot) -> str:
    return _format_period(snapshot, "week")


def format_rop_month(snapshot: RopSnapshot) -> str:
    return _format_period(snapshot, "month")


def format_rop_pipeline(snapshot: RopSnapshot) -> str:
    lines = [
        "ИИ-РОП · текущий pipeline",
        f"• Активных сделок: {snapshot.active_deals}",
    ]
    has_pipeline = False
    for item in snapshot.currencies:
        if item.active_pipeline:
            has_pipeline = True
            lines.append(f"• {item.currency}: {_money(item.active_pipeline)}")
    if not has_pipeline:
        lines.append("• Активный pipeline пуст.")
    lines.append("\nPipeline — сумма OPPORTUNITY только активных сделок на текущем снимке.")
    return "\n".join(lines)


def format_rop_funnel(snapshot: RopSnapshot) -> str:
    lines = [
        "ИИ-РОП · текущее распределение сделок",
        f"• Всего карточек в выбранном scope: {snapshot.deals_total}",
        f"• Активные: {snapshot.active_deals}",
        f"• Успешные: {snapshot.won_deals}",
        f"• Проигранные: {snapshot.lost_deals}",
        (
            "• Историческая конверсия закрытых → продажа: "
            f"{_percent(snapshot.closed_conversion_percent)}"
        ),
    ]

    if snapshot.category_counts:
        lines.append("\nПо воронкам:")
        for category_id, count in snapshot.category_counts[:15]:
            lines.append(f"• {category_label(category_id)}: {count}")

    if snapshot.stage_counts:
        lines.append("\nТоп стадий:")
        for stage_id, count in snapshot.stage_counts[:20]:
            lines.append(f"• {stage_label(stage_id)}: {count}")

    lines.append(
        "\nИсторическая конверсия здесь справочная. Бизнесовую конверсию смотрим "
        "по выбранному периоду и после калибровки воронок."
    )
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
            f"• #{risk.deal_id} | {risk.idle_days} дн. | "
            f"{category_label(risk.category_id)} | {stage_label(risk.stage_id)}\n"
            f"  {amount} {risk.currency} | ответственный ID {risk.assigned_by_id}"
        )

    lines.append("\nКритерий сейчас: последнее MOVED_TIME, затем DATE_MODIFY/DATE_CREATE.")
    return "\n".join(lines)
