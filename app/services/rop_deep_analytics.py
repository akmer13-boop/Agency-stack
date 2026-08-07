from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from app.services.rop_catalog import category_label, stage_label
from app.storage.crm_store import CrmStore


@dataclass(frozen=True, slots=True)
class LossReasonStat:
    category_id: str
    stage_id: str
    count: int


@dataclass(frozen=True, slots=True)
class StageAgingStat:
    category_id: str
    stage_id: str
    active_count: int
    median_days: float
    average_days: float
    attention_count: int
    critical_count: int


@dataclass(frozen=True, slots=True)
class ManagerStat:
    assigned_by_id: str
    active_count: int
    attention_count: int
    critical_count: int
    month_won: int
    month_lost: int
    month_won_amounts: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True, slots=True)
class LossReport:
    label: str
    total_lost: int
    reasons: tuple[LossReasonStat, ...]
    by_category: tuple[tuple[str, int], ...]
    by_manager: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class StageAgingReport:
    active_total: int
    stages: tuple[StageAgingStat, ...]


@dataclass(frozen=True, slots=True)
class ManagerReport:
    managers: tuple[ManagerStat, ...]


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


def _closed_at(item: dict[str, Any]) -> datetime | None:
    for key in ("MOVED_TIME", "DATE_MODIFY"):
        parsed = _datetime(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _last_movement(item: dict[str, Any]) -> datetime | None:
    for key in ("MOVED_TIME", "DATE_MODIFY", "DATE_CREATE"):
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
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC)


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


async def _load_deals(database_path: str) -> list[dict[str, Any]]:
    store = CrmStore(database_path)
    await store.initialize()
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT payload_json
            FROM crm_raw_entities
            WHERE entity_type = 'deal'
            ORDER BY CAST(entity_id AS INTEGER)
            """
        )
        rows = await cursor.fetchall()

    deals: list[dict[str, Any]] = []
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            deals.append(payload)
    return deals


async def build_loss_report(
    database_path: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Moscow",
    included_category_ids: frozenset[str] = frozenset(),
    excluded_stage_ids: frozenset[str] = frozenset(),
) -> LossReport:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    start = _month_start(reference, timezone_name)
    reason_counts: Counter[tuple[str, str]] = Counter()
    category_counts: Counter[str] = Counter()
    manager_counts: Counter[str] = Counter()

    for deal in await _load_deals(database_path):
        if not _in_scope(
            deal,
            included_category_ids=included_category_ids,
            excluded_stage_ids=excluded_stage_ids,
        ):
            continue
        if _semantic(deal) != "F":
            continue
        closed_at = _closed_at(deal)
        if closed_at is None or not start <= closed_at <= reference:
            continue

        category_id = _text(deal.get("CATEGORY_ID"), "0")
        stage_id = _text(deal.get("STAGE_ID"), "не указана")
        manager_id = _text(deal.get("ASSIGNED_BY_ID"), "не назначен")
        reason_counts[(category_id, stage_id)] += 1
        category_counts[category_id] += 1
        manager_counts[manager_id] += 1

    reasons = tuple(
        LossReasonStat(category_id=category_id, stage_id=stage_id, count=count)
        for (category_id, stage_id), count in reason_counts.most_common()
    )
    return LossReport(
        label="Текущий месяц",
        total_lost=sum(reason_counts.values()),
        reasons=reasons,
        by_category=tuple(category_counts.most_common()),
        by_manager=tuple(manager_counts.most_common()),
    )


async def build_stage_aging_report(
    database_path: str,
    *,
    now: datetime | None = None,
    attention_days: int = 3,
    critical_days: int = 5,
    included_category_ids: frozenset[str] = frozenset(),
    excluded_stage_ids: frozenset[str] = frozenset(),
) -> StageAgingReport:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    ages: defaultdict[tuple[str, str], list[int]] = defaultdict(list)

    for deal in await _load_deals(database_path):
        if not _in_scope(
            deal,
            included_category_ids=included_category_ids,
            excluded_stage_ids=excluded_stage_ids,
        ):
            continue
        if _semantic(deal) in {"S", "F"}:
            continue
        moved_at = _last_movement(deal)
        if moved_at is None:
            continue
        age_days = max(0, int((reference - moved_at).total_seconds() // 86400))
        key = (
            _text(deal.get("CATEGORY_ID"), "0"),
            _text(deal.get("STAGE_ID"), "не указана"),
        )
        ages[key].append(age_days)

    stats: list[StageAgingStat] = []
    for (category_id, stage_id), values in ages.items():
        stats.append(
            StageAgingStat(
                category_id=category_id,
                stage_id=stage_id,
                active_count=len(values),
                median_days=float(median(values)),
                average_days=sum(values) / len(values),
                attention_count=sum(value >= attention_days for value in values),
                critical_count=sum(value >= critical_days for value in values),
            )
        )

    stats.sort(
        key=lambda item: (
            item.critical_count,
            item.attention_count,
            item.active_count,
            item.median_days,
        ),
        reverse=True,
    )
    return StageAgingReport(
        active_total=sum(item.active_count for item in stats),
        stages=tuple(stats),
    )


async def build_manager_report(
    database_path: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Moscow",
    attention_days: int = 3,
    critical_days: int = 5,
    included_category_ids: frozenset[str] = frozenset(),
    excluded_stage_ids: frozenset[str] = frozenset(),
) -> ManagerReport:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    start = _month_start(reference, timezone_name)
    counters: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "active": 0,
            "attention": 0,
            "critical": 0,
            "won": 0,
            "lost": 0,
            "won_amounts": defaultdict(lambda: Decimal("0")),
        }
    )

    for deal in await _load_deals(database_path):
        if not _in_scope(
            deal,
            included_category_ids=included_category_ids,
            excluded_stage_ids=excluded_stage_ids,
        ):
            continue
        manager_id = _text(deal.get("ASSIGNED_BY_ID"), "не назначен")
        item = counters[manager_id]
        semantic = _semantic(deal)

        if semantic not in {"S", "F"}:
            item["active"] += 1
            moved_at = _last_movement(deal)
            if moved_at is not None:
                idle = max(0, int((reference - moved_at).total_seconds() // 86400))
                if idle >= attention_days:
                    item["attention"] += 1
                if idle >= critical_days:
                    item["critical"] += 1
            continue

        closed_at = _closed_at(deal)
        if closed_at is None or not start <= closed_at <= reference:
            continue
        if semantic == "S":
            item["won"] += 1
            currency = _text(deal.get("CURRENCY_ID"), "N/A").upper()
            item["won_amounts"][currency] += _decimal(deal.get("OPPORTUNITY"))
        else:
            item["lost"] += 1

    managers: list[ManagerStat] = []
    for manager_id, item in counters.items():
        managers.append(
            ManagerStat(
                assigned_by_id=manager_id,
                active_count=int(item["active"]),
                attention_count=int(item["attention"]),
                critical_count=int(item["critical"]),
                month_won=int(item["won"]),
                month_lost=int(item["lost"]),
                month_won_amounts=tuple(sorted(item["won_amounts"].items())),
            )
        )

    managers.sort(
        key=lambda item: (
            item.critical_count,
            item.attention_count,
            item.active_count,
            item.month_lost,
        ),
        reverse=True,
    )
    return ManagerReport(managers=tuple(managers))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def format_loss_report(report: LossReport, *, limit: int = 15) -> str:
    lines = [
        f"ИИ-РОП · причины проигрышей · {report.label}",
        f"• Проиграно: {report.total_lost}",
        "\nФинальные причины/стадии проигрыша:",
    ]
    for item in report.reasons[:limit]:
        lines.append(
            f"• {category_label(item.category_id)} · "
            f"{stage_label(item.stage_id)}: {item.count}"
        )

    if report.by_category:
        lines.append("\nПо воронкам:")
        for category_id, count in report.by_category[:10]:
            lines.append(f"• {category_label(category_id)}: {count}")

    if report.by_manager:
        lines.append("\nПо ответственным ID:")
        for manager_id, count in report.by_manager[:10]:
            lines.append(f"• ID {manager_id}: {count}")

    lines.append(
        "\nЭто фактическая финальная стадия проигрыша в CRM, а не причина, "
        "выведенная моделью."
    )
    return "\n".join(lines)


def format_stage_aging_report(report: StageAgingReport, *, limit: int = 20) -> str:
    lines = [
        "ИИ-РОП · stage aging активных сделок",
        f"• Активных карточек с датой движения: {report.active_total}",
        "\nСтадии с наибольшим количеством 5+ дней:",
    ]
    for item in report.stages[:limit]:
        lines.append(
            f"• {category_label(item.category_id)} · {stage_label(item.stage_id)} | "
            f"активных {item.active_count} | медиана {item.median_days:.1f} дн. | "
            f"3+ {item.attention_count} | 5+ {item.critical_count}"
        )

    lines.append(
        "\n3+/5+ здесь — кандидаты на внимание по общему нормативу. "
        "Это не SLA-нарушение конкретной стадии, пока для неё не задан свой норматив."
    )
    return "\n".join(lines)


def format_manager_report(report: ManagerReport, *, limit: int = 20) -> str:
    lines = [
        "ИИ-РОП · карточки ответственных",
        "Сортировка: сначала критические активные сделки 5+ дней.",
    ]
    for item in report.managers[:limit]:
        closed = item.month_won + item.month_lost
        conversion = 100 * item.month_won / closed if closed else 0.0
        line = (
            f"• ID {item.assigned_by_id} | активных {item.active_count} | "
            f"3+ {item.attention_count} | 5+ {item.critical_count} | "
            f"месяц WON {item.month_won} / LOST {item.month_lost} | "
            f"конверсия закрытых {conversion:.1f}%"
        )
        lines.append(line)
        if item.month_won_amounts:
            amounts = ", ".join(
                f"{currency} {_money(amount)}"
                for currency, amount in item.month_won_amounts
            )
            lines.append(f"  сумма WON: {amounts}")

    lines.append(
        "\nФИО пока не подставляются: в локальной CRM есть ASSIGNED_BY_ID, "
        "но справочник сотрудников ещё не синхронизирован."
    )
    return "\n".join(lines)
