from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from app.services.rop_catalog import category_label, stage_label
from app.storage.crm_store import CrmStore


@dataclass(frozen=True, slots=True)
class StageRule:
    category_id: str
    stage_id: str
    label: str
    attention_days: int
    critical_days: int


@dataclass(frozen=True, slots=True)
class StageSlaStat:
    rule: StageRule
    active_count: int
    attention_count: int
    critical_count: int
    median_days: float


@dataclass(frozen=True, slots=True)
class QualificationToQuoteStat:
    category_id: str
    start_stage_id: str
    target_stage_id: str
    completed_count: int
    median_hours: float | None
    over_72h_count: int
    active_pending_over_72h: int


@dataclass(frozen=True, slots=True)
class CycleTimeStat:
    category_id: str
    sample_count: int
    median_days: float
    average_days: float


@dataclass(frozen=True, slots=True)
class CycleTimeReport:
    month: tuple[CycleTimeStat, ...]
    trailing_90d: tuple[CycleTimeStat, ...]
    qualification_to_quote: tuple[QualificationToQuoteStat, ...]


@dataclass(frozen=True, slots=True)
class FocusDeal:
    deal_id: str
    category_id: str
    stage_id: str
    assigned_by_id: str
    currency: str
    opportunity: Decimal
    age_days: int
    attention_days: int
    critical_days: int
    severity: str
    rule_label: str


@dataclass(frozen=True, slots=True)
class FocusReport:
    total_candidates: int
    critical_candidates: int
    deals: tuple[FocusDeal, ...]


_STAGE_RULES: tuple[StageRule, ...] = (
    # Qualification / work-up: business target says qualification should complete within 72h.
    StageRule("7", "C7:PREPARATION", "Квалификация → КП", 2, 3),
    StageRule("8", "C8:PREPARATION", "Квалификация → КП", 2, 3),
    StageRule("2", "C2:PREPARATION", "Взято в работу → предложение", 2, 3),
    StageRule("0", "UC_144SIG", "Квалификация → КП", 2, 3),
    StageRule("11", "C11:PREPARATION", "Взято в работу → предложение", 2, 3),
    # Quote/proposal follow-up: business target gives an 8–24 day follow-up window.
    StageRule("7", "C7:EXECUTING", "Follow-up после КП", 8, 24),
    StageRule("8", "C8:PREPAYMENT_INVOICE", "Follow-up после КП", 8, 24),
    StageRule("2", "C2:EXECUTING", "Follow-up после предложения", 8, 24),
    StageRule("0", "PREPAYMENT_INVOICE", "Follow-up после КП", 8, 24),
    StageRule("11", "C11:UC_38DAXX", "Follow-up после предложения", 8, 24),
)

_QUALIFICATION_TO_QUOTE: dict[str, tuple[str, str]] = {
    "7": ("C7:PREPARATION", "C7:EXECUTING"),
    "8": ("C8:PREPARATION", "C8:PREPAYMENT_INVOICE"),
    "2": ("C2:PREPARATION", "C2:EXECUTING"),
    "0": ("UC_144SIG", "PREPAYMENT_INVOICE"),
    "11": ("C11:PREPARATION", "C11:UC_38DAXX"),
}


def _text(value: Any, default: str = "—") -> str:
    if value in (None, ""):
        return default
    return str(value)


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


def _timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _month_start(reference: datetime, timezone_name: str) -> datetime:
    local = reference.astimezone(_timezone(timezone_name))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _in_scope(
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


async def _load_entities(database_path: str, entity_type: str) -> list[dict[str, Any]]:
    store = CrmStore(database_path)
    await store.initialize()
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


def _active_rules_for_scope(
    included_category_ids: frozenset[str],
    excluded_stage_ids: frozenset[str],
) -> tuple[StageRule, ...]:
    return tuple(
        rule
        for rule in _STAGE_RULES
        if (not included_category_ids or rule.category_id in included_category_ids)
        and rule.stage_id not in excluded_stage_ids
    )


async def build_stage_sla_report(
    database_path: str,
    *,
    now: datetime | None = None,
    included_category_ids: frozenset[str] = frozenset(),
    excluded_stage_ids: frozenset[str] = frozenset(),
) -> tuple[StageSlaStat, ...]:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    rules = {
        (rule.category_id, rule.stage_id): rule
        for rule in _active_rules_for_scope(included_category_ids, excluded_stage_ids)
    }
    ages: defaultdict[tuple[str, str], list[int]] = defaultdict(list)

    for deal in await _load_entities(database_path, "deal"):
        if not _in_scope(
            deal,
            included_category_ids=included_category_ids,
            excluded_stage_ids=excluded_stage_ids,
        ):
            continue
        if _semantic(deal) in {"S", "F"}:
            continue
        key = (
            _text(deal.get("CATEGORY_ID"), "0"),
            _text(deal.get("STAGE_ID"), ""),
        )
        if key not in rules:
            continue
        moved_at = _last_movement(deal)
        if moved_at is None:
            continue
        ages[key].append(max(0, int((reference - moved_at).total_seconds() // 86400)))

    stats: list[StageSlaStat] = []
    for key, rule in rules.items():
        values = ages.get(key, [])
        if not values:
            continue
        stats.append(
            StageSlaStat(
                rule=rule,
                active_count=len(values),
                attention_count=sum(value >= rule.attention_days for value in values),
                critical_count=sum(value >= rule.critical_days for value in values),
                median_days=float(median(values)),
            )
        )
    stats.sort(
        key=lambda item: (item.critical_count, item.attention_count, item.active_count),
        reverse=True,
    )
    return tuple(stats)


def _cycle_stats(
    deals: list[dict[str, Any]],
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[CycleTimeStat, ...]:
    by_category: defaultdict[str, list[float]] = defaultdict(list)
    for deal in deals:
        if _semantic(deal) != "S":
            continue
        closed_at = _closed_at(deal)
        created_at = _datetime(deal.get("DATE_CREATE"))
        if closed_at is None or created_at is None or closed_at < created_at:
            continue
        if not start_at <= closed_at <= end_at:
            continue
        category_id = _text(deal.get("CATEGORY_ID"), "0")
        days = (closed_at - created_at).total_seconds() / 86400
        by_category[category_id].append(max(0.0, days))

    result = [
        CycleTimeStat(
            category_id=category_id,
            sample_count=len(values),
            median_days=float(median(values)),
            average_days=sum(values) / len(values),
        )
        for category_id, values in by_category.items()
        if values
    ]
    result.sort(key=lambda item: item.sample_count, reverse=True)
    return tuple(result)


async def build_cycle_time_report(
    database_path: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Moscow",
    included_category_ids: frozenset[str] = frozenset(),
    excluded_stage_ids: frozenset[str] = frozenset(),
) -> CycleTimeReport:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    month_start = _month_start(reference, timezone_name)
    trailing_start = reference - timedelta(days=90)
    deals = [
        deal
        for deal in await _load_entities(database_path, "deal")
        if _in_scope(
            deal,
            included_category_ids=included_category_ids,
            excluded_stage_ids=excluded_stage_ids,
        )
    ]
    deals_by_id = {_text(deal.get("ID"), ""): deal for deal in deals}
    histories = await _load_entities(database_path, "deal_stage_history")

    events_by_owner: defaultdict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for event in histories:
        owner_id = _text(event.get("OWNER_ID"), "")
        created_at = _datetime(event.get("CREATED_TIME"))
        stage_id = _text(event.get("STAGE_ID"), "")
        if not owner_id or created_at is None or not stage_id:
            continue
        if owner_id not in deals_by_id:
            continue
        events_by_owner[owner_id].append((created_at, stage_id))
    for events in events_by_owner.values():
        events.sort(key=lambda item: item[0])

    transition_stats: list[QualificationToQuoteStat] = []
    for category_id, (start_stage, target_stage) in _QUALIFICATION_TO_QUOTE.items():
        if included_category_ids and category_id not in included_category_ids:
            continue
        if start_stage in excluded_stage_ids or target_stage in excluded_stage_ids:
            continue

        completed_hours: list[float] = []
        over_72h = 0
        pending_over_72h = 0
        for deal_id, deal in deals_by_id.items():
            if _text(deal.get("CATEGORY_ID"), "0") != category_id:
                continue
            events = events_by_owner.get(deal_id, [])
            starts = [at for at, stage_id in events if stage_id == start_stage]
            if not starts:
                continue
            start_at = starts[0]
            targets = [
                at
                for at, stage_id in events
                if stage_id == target_stage and at >= start_at
            ]
            if targets:
                hours = max(0.0, (targets[0] - start_at).total_seconds() / 3600)
                completed_hours.append(hours)
                if hours > 72:
                    over_72h += 1
                continue
            if _semantic(deal) not in {"S", "F"}:
                pending_hours = max(0.0, (reference - start_at).total_seconds() / 3600)
                if pending_hours > 72:
                    pending_over_72h += 1

        if completed_hours or pending_over_72h:
            transition_stats.append(
                QualificationToQuoteStat(
                    category_id=category_id,
                    start_stage_id=start_stage,
                    target_stage_id=target_stage,
                    completed_count=len(completed_hours),
                    median_hours=(
                        float(median(completed_hours)) if completed_hours else None
                    ),
                    over_72h_count=over_72h,
                    active_pending_over_72h=pending_over_72h,
                )
            )

    return CycleTimeReport(
        month=_cycle_stats(deals, start_at=month_start, end_at=reference),
        trailing_90d=_cycle_stats(deals, start_at=trailing_start, end_at=reference),
        qualification_to_quote=tuple(transition_stats),
    )


async def build_focus_report(
    database_path: str,
    *,
    now: datetime | None = None,
    limit: int = 20,
    included_category_ids: frozenset[str] = frozenset(),
    excluded_stage_ids: frozenset[str] = frozenset(),
) -> FocusReport:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    rules = {
        (rule.category_id, rule.stage_id): rule
        for rule in _active_rules_for_scope(included_category_ids, excluded_stage_ids)
    }
    candidates: list[FocusDeal] = []

    for deal in await _load_entities(database_path, "deal"):
        if not _in_scope(
            deal,
            included_category_ids=included_category_ids,
            excluded_stage_ids=excluded_stage_ids,
        ):
            continue
        if _semantic(deal) in {"S", "F"}:
            continue
        category_id = _text(deal.get("CATEGORY_ID"), "0")
        stage_id = _text(deal.get("STAGE_ID"), "")
        rule = rules.get((category_id, stage_id))
        if rule is None:
            continue
        moved_at = _last_movement(deal)
        if moved_at is None:
            continue
        age_days = max(0, int((reference - moved_at).total_seconds() // 86400))
        if age_days < rule.attention_days:
            continue
        severity = "critical" if age_days >= rule.critical_days else "attention"
        candidates.append(
            FocusDeal(
                deal_id=_text(deal.get("ID"), "?"),
                category_id=category_id,
                stage_id=stage_id,
                assigned_by_id=_text(deal.get("ASSIGNED_BY_ID"), "не назначен"),
                currency=_text(deal.get("CURRENCY_ID"), "N/A").upper(),
                opportunity=_decimal(deal.get("OPPORTUNITY")),
                age_days=age_days,
                attention_days=rule.attention_days,
                critical_days=rule.critical_days,
                severity=severity,
                rule_label=rule.label,
            )
        )

    candidates.sort(
        key=lambda item: (
            item.severity == "critical",
            item.age_days - item.critical_days,
            item.age_days,
        ),
        reverse=True,
    )
    return FocusReport(
        total_candidates=len(candidates),
        critical_candidates=sum(item.severity == "critical" for item in candidates),
        deals=tuple(candidates[:limit]),
    )


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def format_stage_sla_report(stats: tuple[StageSlaStat, ...]) -> str:
    lines = [
        "ИИ-РОП · stage-specific SLA",
        "Используются только нормативы, которые уже есть в бизнес-ТЗ.",
    ]
    if not stats:
        lines.append("\nАктивные сделки на измеряемых SLA-стадиях не найдены.")
        return "\n".join(lines)

    lines.append("\nИзмеряемые стадии:")
    for item in stats:
        rule = item.rule
        lines.append(
            f"• {category_label(rule.category_id)} · {stage_label(rule.stage_id)} | "
            f"активных {item.active_count} | медиана {item.median_days:.1f} дн. | "
            f"внимание {item.attention_count} (≥{rule.attention_days} дн.) | "
            f"критично {item.critical_count} (≥{rule.critical_days} дн.)"
        )
    lines.append(
        "\nКвалификация: критический порог 72 ч. КП/предложение: окно follow-up "
        "8–24 дня. SLA первого содержательного ответа 15 мин здесь не считается."
    )
    return "\n".join(lines)


def _format_cycle_section(title: str, stats: tuple[CycleTimeStat, ...]) -> list[str]:
    lines = [title]
    if not stats:
        lines.append("• нет достаточных WON с корректными датами")
        return lines
    for item in stats:
        lines.append(
            f"• {category_label(item.category_id)} | n={item.sample_count} | "
            f"медиана {item.median_days:.1f} дн. | среднее {item.average_days:.1f} дн."
        )
    return lines


def format_cycle_time_report(report: CycleTimeReport) -> str:
    lines = ["ИИ-РОП · cycle time"]
    lines.extend(_format_cycle_section("\nWON текущего месяца:", report.month))
    lines.extend(_format_cycle_section("\nWON за последние 90 дней:", report.trailing_90d))

    lines.append("\nКвалификация → КП/предложение по deal_stage_history:")
    if not report.qualification_to_quote:
        lines.append("• переходы не найдены")
    for item in report.qualification_to_quote:
        median_text = (
            f"{item.median_hours:.1f} ч"
            if item.median_hours is not None
            else "нет завершённых переходов"
        )
        lines.append(
            f"• {category_label(item.category_id)} | завершено n={item.completed_count} | "
            f"медиана {median_text} | завершено >72ч {item.over_72h_count} | "
            f"активных без КП >72ч {item.active_pending_over_72h}"
        )
    lines.append(
        "\nCycle time = DATE_CREATE → финальное закрытие WON. Переход квалификация→КП "
        "считается по локальной истории стадий, без LLM."
    )
    return "\n".join(lines)


def format_focus_report(report: FocusReport) -> str:
    lines = [
        "ИИ-РОП · focus-list на сегодня",
        f"• кандидатов по измеряемым SLA: {report.total_candidates}",
        f"• критических: {report.critical_candidates}",
    ]
    if not report.deals:
        lines.append("\nКандидаты по текущим SLA-правилам не найдены.")
        return "\n".join(lines)

    lines.append("\nПриоритетные карточки:")
    for item in report.deals:
        severity = "КРИТИЧНО" if item.severity == "critical" else "ВНИМАНИЕ"
        lines.append(
            f"• [{severity}] #{item.deal_id} | {category_label(item.category_id)} · "
            f"{stage_label(item.stage_id)} | {item.age_days} дн. | "
            f"{_money(item.opportunity)} {item.currency} | отв. ID {item.assigned_by_id} | "
            f"{item.rule_label}"
        )
    lines.append(
        "\nСортировка учитывает тяжесть SLA и длительность. Суммы разных валют между "
        "собой не сравниваются и не используются как единая выручка."
    )
    return "\n".join(lines)
