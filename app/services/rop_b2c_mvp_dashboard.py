from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.rop_b2c_first_response_truth import (
    B2CFirstResponseTruth,
    build_b2c_first_response_truth,
)
from app.services.rop_b2c_stage_sla_truth import (
    STAGE_LABELS,
    B2CStageSlaTruth,
    build_b2c_stage_sla_truth,
)
from app.services.rop_policy_engine import load_policy_contract
from app.services.rop_policy_scope import CONCIERGE_DEPARTMENT_IDS

MOSCOW = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True, slots=True)
class B2CManagerMvpSummary:
    manager_id: str
    manager_name: str
    active_deals: int
    month_new_deals: int
    month_won: int
    month_lost: int
    stage_attention: int
    first_response_breaches: int

    @property
    def closed_deals(self) -> int:
        return self.month_won + self.month_lost

    @property
    def conversion_percent(self) -> float:
        if self.closed_deals <= 0:
            return 0.0
        return 100.0 * self.month_won / self.closed_deals


@dataclass(frozen=True, slots=True)
class B2CMvpDashboard:
    cutoff_at: datetime
    month_start: datetime
    active_b2c_deals: int
    month_new_deals: int
    month_won: int
    month_lost: int
    won_revenue_by_currency: tuple[tuple[str, Decimal], ...]
    first_response: B2CFirstResponseTruth
    stage_sla: B2CStageSlaTruth
    managers: tuple[B2CManagerMvpSummary, ...]

    @property
    def closed_conversion_percent(self) -> float:
        closed = self.month_won + self.month_lost
        if closed <= 0:
            return 0.0
        return 100.0 * self.month_won / closed


@dataclass(frozen=True, slots=True)
class B2CPeriodFlow:
    window_start: datetime
    window_end: datetime
    new_deals: int
    won: int
    lost: int

    @property
    def closed(self) -> int:
        return self.won + self.lost

    @property
    def conversion_percent(self) -> float:
        if self.closed <= 0:
            return 0.0
        return 100.0 * self.won / self.closed


def _connect(database_path: str) -> sqlite3.Connection:
    path = Path(database_path).resolve()
    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _department_ids(value: Any) -> frozenset[str]:
    if value in (None, ""):
        return frozenset()
    if isinstance(value, (list, tuple, set)):
        return frozenset(
            str(item).strip()
            for item in value
            if item not in (None, "")
        )
    return frozenset({str(value).strip()})


def _tourism_category_id() -> str:
    contract = load_policy_contract()
    funnel = contract.binding.get("business_policy_funnel")
    if not isinstance(funnel, dict):
        return ""
    return str(funnel.get("category_id") or "").strip()


def _manager_name(
    users: dict[str, dict[str, Any]],
    manager_id: str,
) -> str:
    payload = users.get(manager_id)
    if payload is None:
        return f"Менеджер #{manager_id}" if manager_id else "Не назначен"

    name = " ".join(
        part
        for part in (
            str(payload.get("NAME") or "").strip(),
            str(payload.get("LAST_NAME") or "").strip(),
        )
        if part
    ).strip()

    if name:
        return name

    login = str(payload.get("LOGIN") or "").strip()
    if login:
        return login

    return f"Менеджер #{manager_id}" if manager_id else "Не назначен"


def _closed_at(item: dict[str, Any]) -> datetime | None:
    for key in ("MOVED_TIME", "DATE_MODIFY"):
        parsed = _dt(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_closed(item: dict[str, Any]) -> bool:
    return str(
        item.get("STAGE_SEMANTIC_ID") or "P"
    ).strip().upper() in {"S", "F"}


def _eligible_deals(
    database_path: str,
) -> tuple[
    dict[int, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    connection = _connect(database_path)

    try:
        users: dict[str, dict[str, Any]] = {}
        user_departments: dict[str, frozenset[str]] = {}

        for row in connection.execute(
            """
            SELECT entity_id, payload_json
            FROM crm_active_entities
            WHERE entity_type = 'user'
            """
        ):
            item = _payload(row["payload_json"])
            if item is None:
                continue
            user_id = str(row["entity_id"])
            users[user_id] = item
            user_departments[user_id] = _department_ids(
                item.get("UF_DEPARTMENT")
            )

        tourism_category = _tourism_category_id()
        deals: dict[int, dict[str, Any]] = {}

        for row in connection.execute(
            """
            SELECT entity_id, payload_json
            FROM crm_active_entities
            WHERE entity_type = 'deal'
            """
        ):
            item = _payload(row["payload_json"])
            if item is None:
                continue

            if str(
                item.get("CATEGORY_ID") or ""
            ).strip() != tourism_category:
                continue

            manager_id = str(
                item.get("ASSIGNED_BY_ID")
                or item.get("RESPONSIBLE_ID")
                or ""
            ).strip()

            if (
                manager_id
                and user_departments.get(
                    manager_id,
                    frozenset(),
                )
                & CONCIERGE_DEPARTMENT_IDS
            ):
                continue

            try:
                deal_id = int(row["entity_id"])
            except (TypeError, ValueError):
                continue

            if deal_id > 0:
                deals[deal_id] = item

        return deals, users
    finally:
        connection.close()


def build_b2c_period_flow(
    settings: Settings,
    *,
    window_start: datetime,
    window_end: datetime,
) -> B2CPeriodFlow:
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("period boundaries must be timezone-aware")

    start = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    if start > end:
        raise ValueError("window_start must not be after window_end")

    deals, _users = _eligible_deals(settings.database_path)
    new_deals = 0
    won = 0
    lost = 0

    for deal in deals.values():
        created = _dt(deal.get("DATE_CREATE"))
        if created is not None and start <= created <= end:
            new_deals += 1

        semantic = str(
            deal.get("STAGE_SEMANTIC_ID") or "P"
        ).strip().upper()
        if semantic not in {"S", "F"}:
            continue

        closed = _closed_at(deal)
        if closed is None or not start <= closed <= end:
            continue

        if semantic == "S":
            won += 1
        else:
            lost += 1

    return B2CPeriodFlow(
        window_start=start,
        window_end=end,
        new_deals=new_deals,
        won=won,
        lost=lost,
    )


def build_b2c_mvp_dashboard(
    settings: Settings,
) -> B2CMvpDashboard:
    stage_sla = build_b2c_stage_sla_truth(
        settings.database_path
    )

    cutoff = stage_sla.cutoff_at
    local_cutoff = cutoff.astimezone(MOSCOW)
    month_start = local_cutoff.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ).astimezone(UTC)

    first_response = build_b2c_first_response_truth(
        settings.database_path,
        now=cutoff,
    )

    deals, users = _eligible_deals(
        settings.database_path
    )

    active_b2c_deals = 0
    month_new_deals = 0
    month_won = 0
    month_lost = 0
    won_revenue: defaultdict[str, Decimal] = defaultdict(
        lambda: Decimal("0")
    )

    manager_counts: dict[str, Counter[str]] = defaultdict(
        Counter
    )

    for deal in deals.values():
        manager_id = str(
            deal.get("ASSIGNED_BY_ID")
            or deal.get("RESPONSIBLE_ID")
            or ""
        ).strip()

        created = _dt(deal.get("DATE_CREATE"))
        semantic = str(
            deal.get("STAGE_SEMANTIC_ID") or "P"
        ).strip().upper()

        if not _is_closed(deal):
            active_b2c_deals += 1
            manager_counts[manager_id]["active"] += 1

        if (
            created is not None
            and month_start <= created <= cutoff
        ):
            month_new_deals += 1
            manager_counts[manager_id]["new"] += 1

        if semantic not in {"S", "F"}:
            continue

        closed = _closed_at(deal)
        if (
            closed is None
            or closed < month_start
            or closed > cutoff
        ):
            continue

        if semantic == "S":
            month_won += 1
            manager_counts[manager_id]["won"] += 1
            currency = str(
                deal.get("CURRENCY_ID") or "N/A"
            ).strip().upper()
            won_revenue[currency] += _decimal(
                deal.get("OPPORTUNITY")
            )
        else:
            month_lost += 1
            manager_counts[manager_id]["lost"] += 1

    stage_attention = {
        manager_id: count
        for manager_id, _name, count
        in stage_sla.attention_by_manager
    }

    first_response_breaches = {
        str(manager_id): count
        for manager_id, count
        in first_response.breach_by_manager
    }

    manager_ids = (
        set(manager_counts)
        | set(stage_attention)
        | set(first_response_breaches)
    )

    managers = tuple(
        sorted(
            (
                B2CManagerMvpSummary(
                    manager_id=manager_id,
                    manager_name=_manager_name(
                        users,
                        manager_id,
                    ),
                    active_deals=manager_counts[
                        manager_id
                    ]["active"],
                    month_new_deals=manager_counts[
                        manager_id
                    ]["new"],
                    month_won=manager_counts[
                        manager_id
                    ]["won"],
                    month_lost=manager_counts[
                        manager_id
                    ]["lost"],
                    stage_attention=stage_attention.get(
                        manager_id,
                        0,
                    ),
                    first_response_breaches=(
                        first_response_breaches.get(
                            manager_id,
                            0,
                        )
                    ),
                )
                for manager_id in manager_ids
            ),
            key=lambda item: (
                item.stage_attention,
                item.first_response_breaches,
                item.active_deals,
                item.month_new_deals,
                item.month_won,
            ),
            reverse=True,
        )
    )

    return B2CMvpDashboard(
        cutoff_at=cutoff,
        month_start=month_start,
        active_b2c_deals=active_b2c_deals,
        month_new_deals=month_new_deals,
        month_won=month_won,
        month_lost=month_lost,
        won_revenue_by_currency=tuple(
            sorted(won_revenue.items())
        ),
        first_response=first_response,
        stage_sla=stage_sla,
        managers=managers,
    )


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(
        ",",
        " ",
    )


def _percent(
    numerator: int,
    denominator: int,
) -> float:
    if denominator <= 0:
        return 0.0
    return 100.0 * numerator / denominator


def format_b2c_mvp_summary(
    dashboard: B2CMvpDashboard,
) -> str:
    """Format the compact Telegram entry point without changing truth math."""
    fr = dashboard.first_response
    stage = dashboard.stage_sla

    policy_not_configured = dict(
        stage.blocked_reasons
    ).get(
        "return_to_client_date_not_configured",
        0,
    )
    evidence_blocked = max(
        0,
        stage.blocked - policy_not_configured,
    )

    configured_stages = (
        row
        for row in stage.by_stage
        if row[0] != "C7:FINAL_INVOICE"
    )
    top_attention_stage = max(
        configured_stages,
        key=lambda row: row[3],
        default=None,
    )

    lines = [
        "ИИ-РОП · B2C",
        (
            "Срез данных: "
            + dashboard.cutoff_at.astimezone(
                MOSCOW
            ).strftime("%d.%m.%Y %H:%M МСК")
        ),
        "",
        "Поток месяца",
        (
            f"• лиды: {fr.b2c_proven} "
            f"· новые сделки: {dashboard.month_new_deals}"
        ),
        (
            f"• WON / LOST: {dashboard.month_won} / "
            f"{dashboard.month_lost} "
            f"· конверсия {dashboard.closed_conversion_percent:.1f}% "
            f"(n={dashboard.month_won + dashboard.month_lost})"
        ),
        "",
        "Первый ответ · 15 бизнес-минут",
        (
            f"• соблюдение: {fr.ok_share_closed_percent:.1f}% "
            f"(n={fr.closed_measured})"
        ),
        (
            f"• нарушений: {fr.breach} "
            f"· недостаточно данных: {fr.blocked}"
        ),
        "",
        "Активные сделки",
        f"• всего: {dashboard.active_b2c_deals}",
        (
            f"• требуют внимания: {stage.attention} "
            f"· недостаточно данных: {evidence_blocked}"
        ),
        f"• SLA пока не настроен: {policy_not_configured}",
    ]

    if top_attention_stage is not None:
        stage_id, _, _, attention_count, _ = top_attention_stage
        if attention_count > 0:
            lines.extend(
                [
                    "",
                    "Главная проблемная стадия",
                    (
                        f"• {STAGE_LABELS.get(stage_id, stage_id)}: "
                        f"{attention_count} требуют внимания"
                    ),
                ]
            )

    lines.extend(
        [
            "",
            "Подробности — в разделах под сообщением.",
            (
                "💬 ИИ-анализ: напишите вопрос обычным сообщением, "
                "например «Что проверить сегодня?»"
            ),
            "Данные: локальная CRM-копия · Bitrix24 не изменяется.",
        ]
    )

    return "\n".join(lines)


def format_b2c_mvp_dashboard(
    dashboard: B2CMvpDashboard,
) -> str:
    fr = dashboard.first_response
    stage = dashboard.stage_sla

    lines = [
        "ИИ-РОП · B2C Dashboard",
        (
            "Срез данных: "
            + dashboard.cutoff_at.astimezone(
                MOSCOW
            ).strftime("%d.%m.%Y %H:%M МСК")
        ),
        "",
        "B2C · текущий месяц",
        f"• подтверждённые B2C-лиды: {fr.b2c_proven}",
        f"• активные B2C-сделки: {dashboard.active_b2c_deals}",
        f"• новые B2C-сделки: {dashboard.month_new_deals}",
        (
            f"• WON / LOST: "
            f"{dashboard.month_won} / {dashboard.month_lost}"
        ),
        (
            "• конверсия закрытых: "
            f"{dashboard.closed_conversion_percent:.1f}% "
            f"(n={dashboard.month_won + dashboard.month_lost})"
        ),
    ]

    if dashboard.won_revenue_by_currency:
        revenue = ", ".join(
            f"{currency} {_money(amount)}"
            for currency, amount
            in dashboard.won_revenue_by_currency
        )
        lines.append(
            f"• выручка WON: {revenue}"
        )

    lines.extend(
        [
            "",
            "First Response SLA · 15 бизнес-минут",
            (
                f"• измерено: {fr.measured} из "
                f"{fr.b2c_proven} "
                f"({fr.measured_share_percent:.1f}%)"
            ),
            f"• в срок: {fr.ok}",
            f"• нарушение: {fr.breach}",
            (
                "• с подтверждённым менеджером: "
                f"{max(0, fr.breach - getattr(fr, 'unattributed_breaches', 0))}"
            ),
            (
                "• без безопасной атрибуции менеджеру: "
                f"{getattr(fr, 'unattributed_breaches', 0)}"
            ),
            f"• ещё открыто: {fr.open}",
            (
                "• недостаточно данных: "
                f"{fr.blocked}"
            ),
            (
                "• соблюдение среди измеренных закрытых: "
                f"{fr.ok_share_closed_percent:.1f}% "
                f"(n={fr.closed_measured})"
            ),
        ]
    )

    if fr.blocked:
        lines.append(
            "• недостаточно данных НЕ считается нарушением"
        )

    policy_not_configured = dict(
        stage.blocked_reasons
    ).get(
        "return_to_client_date_not_configured",
        0,
    )
    evidence_blocked = max(
        0,
        stage.blocked - policy_not_configured,
    )

    lines.extend(
        [
            "",
            "Stage SLA · контролируемые B2C-сделки",
            f"• в контроле: {stage.tracked_deals}",
            f"• таймер не достигнут: {stage.open}",
            f"• требуют внимания: {stage.attention}",
            (
                "• недостаточно данных для безопасной оценки: "
                f"{evidence_blocked}"
            ),
        ]
    )

    for (
        stage_id,
        total,
        open_count,
        attention_count,
        blocked_count,
    ) in stage.by_stage:
        label = STAGE_LABELS.get(
            stage_id,
            stage_id,
        )

        if stage_id == "C7:FINAL_INVOICE":
            lines.append(
                f"• {label}: {total} · SLA пока не настроен"
            )
            continue

        lines.append(
            f"• {label}: {total} "
            f"· в срок {open_count} "
            f"· внимание {attention_count} "
            f"· недостаточно данных {blocked_count}"
        )

    lines.extend(
        [
            "",
            "Менеджеры · приоритет разбора",
        ]
    )

    visible_managers = [
        item
        for item in dashboard.managers
        if (
            item.stage_attention > 0
            or item.first_response_breaches > 0
        )
    ]

    if not visible_managers:
        lines.append(
            "• сейчас нет подтверждённых SLA-сигналов"
        )
    else:
        for item in visible_managers[:8]:
            lines.append(
                f"• {item.manager_name}: "
                f"требуют внимания {item.stage_attention} "
                f"· нарушений первого ответа "
                f"{item.first_response_breaches} "
                f"· активных сделок {item.active_deals} "
                f"· WON/LOST "
                f"{item.month_won}/{item.month_lost}"
            )

    attention_deals = sorted(
        (
            item
            for item in stage.deals
            if item.status == "ATTENTION"
        ),
        key=lambda item: (
            item.deadline_at
            or datetime.max.replace(
                tzinfo=UTC
            ),
            item.manager_name,
            item.deal_id,
        ),
    )

    lines.extend(
        [
            "",
            "Самые просроченные сделки по Stage SLA",
        ]
    )

    if not attention_deals:
        lines.append(
            "• сейчас нет сделок со Stage Attention"
        )
    else:
        for item in attention_deals[:10]:
            deadline = (
                item.deadline_at.astimezone(
                    MOSCOW
                ).strftime("%d.%m %H:%M")
                if item.deadline_at is not None
                else "—"
            )
            lines.append(
                f"• Сделка #{item.deal_id} · "
                f"{item.stage_label} · "
                f"{item.manager_name} · "
                f"срок {deadline}"
            )

    lines.extend(
        [
            "",
            (
                "Статусы Stage SLA: "
                "таймер не достигнут / требует внимания / "
                "недостаточно данных."
            ),
            (
                "Система анализирует CRM в read-only режиме "
                "и не изменяет Bitrix24."
            ),
        ]
    )

    return "\n".join(lines)
