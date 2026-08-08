from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from app.config import Settings
from app.integrations.bitrix24.client import Bitrix24ReadOnlyClient, Bitrix24RequestError
from app.proxy import build_proxy_url
from app.services.rop_catalog import category_label, stage_label
from app.services.rop_directory import RopDirectory, employee_label, load_rop_directory
from app.services.rop_mvp3 import build_focus_report
from app.storage.crm_store import CrmStore

_ACTIVITY_TYPE_LABELS = {
    "0": "Активность",
    "1": "Встреча",
    "2": "Звонок",
    "3": "Задача",
    "4": "E-mail",
    "5": "Действие",
}
_TIMELINE_COMMENT_METHOD = "crm.timeline.comment.list"


@dataclass(frozen=True, slots=True)
class DealActivity:
    activity_id: str
    activity_type: str
    subject: str
    completed: bool
    event_at: datetime | None
    deadline: datetime | None
    responsible_id: str


@dataclass(frozen=True, slots=True)
class DealStageEvent:
    stage_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class DealTimelineComment:
    comment_id: str
    created_at: datetime | None
    author_id: str
    text: str


@dataclass(frozen=True, slots=True)
class DealDrilldown:
    deal_id: str
    title: str
    category_id: str
    stage_id: str
    semantic_id: str
    opportunity: Decimal
    currency: str
    assigned_by_id: str
    created_at: datetime | None
    last_movement_at: datetime | None
    stage_age_days: int | None
    sla_severity: str | None
    sla_rule_label: str | None
    activities_count: int
    last_completed_activity: DealActivity | None
    next_open_activity: DealActivity | None
    stage_history: tuple[DealStageEvent, ...]
    directory: RopDirectory
    timeline_comments: tuple[DealTimelineComment, ...] = ()
    timeline_error: str | None = None


class DealDetailBitrix24Client(Bitrix24ReadOnlyClient):
    """Narrow read-only client for a deal's timeline comments."""

    def _endpoint(self, method: str) -> str:
        if method == _TIMELINE_COMMENT_METHOD:
            return f"{self._webhook_url}{method}.json"
        return super()._endpoint(method)

    async def list_deal_timeline_comments(
        self,
        deal_id: str,
        *,
        max_items: int = 5,
    ) -> list[dict[str, Any]]:
        return await self.call_all(
            _TIMELINE_COMMENT_METHOD,
            {
                "filter": {
                    "ENTITY_ID": int(deal_id),
                    "ENTITY_TYPE": "deal",
                },
                "select": [
                    "ID",
                    "CREATED",
                    "ENTITY_ID",
                    "ENTITY_TYPE",
                    "AUTHOR_ID",
                    "COMMENT",
                ],
                "order": {"CREATED": "DESC"},
            },
            max_items=max_items,
        )


def build_deal_detail_client(settings: Settings) -> DealDetailBitrix24Client:
    return DealDetailBitrix24Client(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
    )


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
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _is_completed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "y", "yes"}


def _last_movement(deal: dict[str, Any]) -> datetime | None:
    for key in ("MOVED_TIME", "DATE_MODIFY", "DATE_CREATE"):
        parsed = _datetime(deal.get(key))
        if parsed is not None:
            return parsed
    return None


def _clean_text(value: Any, *, limit: int = 260) -> str:
    raw = html.unescape(str(value or ""))
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\[(?:/?[a-zA-Z][^\]]*)\]", " ", raw)
    raw = " ".join(raw.split())
    if not raw:
        return "—"
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


async def _load_payload_by_id(
    database_path: str,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any] | None:
    store = CrmStore(database_path)
    await store.initialize()
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT payload_json
            FROM crm_raw_entities
            WHERE entity_type = ? AND entity_id = ?
            LIMIT 1
            """,
            (entity_type, entity_id),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


async def _load_related_payloads(
    database_path: str,
    entity_type: str,
    owner_id: str,
    *,
    owner_type_id: int | None = None,
) -> list[dict[str, Any]]:
    store = CrmStore(database_path)
    await store.initialize()
    clauses = [
        "entity_type = ?",
        "CAST(json_extract(payload_json, '$.OWNER_ID') AS TEXT) = ?",
    ]
    params: list[Any] = [entity_type, owner_id]
    if owner_type_id is not None:
        clauses.append("CAST(json_extract(payload_json, '$.OWNER_TYPE_ID') AS INTEGER) = ?")
        params.append(owner_type_id)

    query = (
        "SELECT payload_json FROM crm_raw_entities WHERE "
        + " AND ".join(clauses)
        + " ORDER BY CAST(entity_id AS INTEGER)"
    )
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(query, tuple(params))
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


def _activity_type(item: dict[str, Any]) -> str:
    type_id = _text(item.get("TYPE_ID"), "0")
    provider = _text(item.get("PROVIDER_ID"), "")
    label = _ACTIVITY_TYPE_LABELS.get(type_id, f"Тип {type_id}")
    if provider and provider not in {"—", ""} and provider.lower() not in label.lower():
        return f"{label} ({provider})"
    return label


def _activity_event_at(item: dict[str, Any]) -> datetime | None:
    for key in ("END_TIME", "START_TIME", "DEADLINE", "LAST_UPDATED", "CREATED"):
        parsed = _datetime(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _activity_deadline(item: dict[str, Any]) -> datetime | None:
    for key in ("DEADLINE", "START_TIME", "END_TIME"):
        parsed = _datetime(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _to_activity(item: dict[str, Any]) -> DealActivity:
    return DealActivity(
        activity_id=_text(item.get("ID"), "?"),
        activity_type=_activity_type(item),
        subject=_clean_text(item.get("SUBJECT"), limit=180),
        completed=_is_completed(item.get("COMPLETED")),
        event_at=_activity_event_at(item),
        deadline=_activity_deadline(item),
        responsible_id=_text(item.get("RESPONSIBLE_ID"), "не назначен"),
    )


def _stage_events(items: list[dict[str, Any]]) -> tuple[DealStageEvent, ...]:
    events: list[DealStageEvent] = []
    for item in items:
        occurred_at = _datetime(item.get("CREATED_TIME"))
        stage_id = _text(item.get("STAGE_ID"), "")
        if occurred_at is None or not stage_id:
            continue
        events.append(DealStageEvent(stage_id=stage_id, occurred_at=occurred_at))
    events.sort(key=lambda item: item.occurred_at)

    collapsed: list[DealStageEvent] = []
    for event in events:
        if collapsed and collapsed[-1].stage_id == event.stage_id:
            continue
        collapsed.append(event)
    return tuple(collapsed)


def _timeline_comments(items: list[dict[str, Any]]) -> tuple[DealTimelineComment, ...]:
    result: list[DealTimelineComment] = []
    for item in items:
        result.append(
            DealTimelineComment(
                comment_id=_text(item.get("ID"), "?"),
                created_at=_datetime(item.get("CREATED")),
                author_id=_text(item.get("AUTHOR_ID"), "неизвестен"),
                text=_clean_text(item.get("COMMENT")),
            )
        )
    return tuple(result)


async def build_deal_drilldown(
    settings: Settings,
    deal_id: str | int,
    *,
    now: datetime | None = None,
    include_timeline_comments: bool = False,
) -> DealDrilldown | None:
    normalized_id = str(deal_id).strip()
    if not normalized_id.isdigit():
        return None

    deal = await _load_payload_by_id(settings.database_path, "deal", normalized_id)
    if deal is None:
        return None

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    activities_payload = await _load_related_payloads(
        settings.database_path,
        "activity",
        normalized_id,
        owner_type_id=2,
    )
    history_payload = await _load_related_payloads(
        settings.database_path,
        "deal_stage_history",
        normalized_id,
    )
    activities = [_to_activity(item) for item in activities_payload]

    completed = [item for item in activities if item.completed and item.event_at is not None]
    last_completed = max(completed, key=lambda item: item.event_at or datetime.min.replace(tzinfo=UTC)) if completed else None

    open_activities = [item for item in activities if not item.completed]
    with_deadline = [item for item in open_activities if item.deadline is not None]
    if with_deadline:
        next_open = min(with_deadline, key=lambda item: item.deadline or datetime.max.replace(tzinfo=UTC))
    elif open_activities:
        next_open = max(
            open_activities,
            key=lambda item: item.event_at or datetime.min.replace(tzinfo=UTC),
        )
    else:
        next_open = None

    focus = await build_focus_report(
        settings.database_path,
        now=reference,
        limit=10_000,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )
    focus_item = next((item for item in focus.deals if item.deal_id == normalized_id), None)

    last_movement = _last_movement(deal)
    stage_age_days = (
        max(0, int((reference - last_movement).total_seconds() // 86400))
        if last_movement is not None
        else None
    )
    directory = await load_rop_directory(settings.database_path)

    comments: tuple[DealTimelineComment, ...] = ()
    timeline_error: str | None = None
    if include_timeline_comments and settings.bitrix24_configured:
        try:
            raw_comments = await build_deal_detail_client(settings).list_deal_timeline_comments(
                normalized_id,
                max_items=5,
            )
            comments = _timeline_comments(raw_comments)
        except Bitrix24RequestError as exc:
            timeline_error = exc.error_code or "BITRIX24_REQUEST_ERROR"

    return DealDrilldown(
        deal_id=normalized_id,
        title=_clean_text(deal.get("TITLE"), limit=180),
        category_id=_text(deal.get("CATEGORY_ID"), "0"),
        stage_id=_text(deal.get("STAGE_ID"), ""),
        semantic_id=_text(deal.get("STAGE_SEMANTIC_ID"), "P").upper(),
        opportunity=_decimal(deal.get("OPPORTUNITY")),
        currency=_text(deal.get("CURRENCY_ID"), "N/A").upper(),
        assigned_by_id=_text(deal.get("ASSIGNED_BY_ID"), "не назначен"),
        created_at=_datetime(deal.get("DATE_CREATE")),
        last_movement_at=last_movement,
        stage_age_days=stage_age_days,
        sla_severity=focus_item.severity if focus_item is not None else None,
        sla_rule_label=focus_item.rule_label if focus_item is not None else None,
        activities_count=len(activities),
        last_completed_activity=last_completed,
        next_open_activity=next_open,
        stage_history=_stage_events(history_payload),
        directory=directory,
        timeline_comments=comments,
        timeline_error=timeline_error,
    )


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _format_dt(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "—"
    return value.astimezone(_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")


def _employee(directory: RopDirectory, user_id: str, *, include_id: bool = True) -> str:
    return employee_label(directory, user_id, include_id=include_id)


def _sla_text(report: DealDrilldown) -> str:
    if report.semantic_id in {"S", "F"}:
        return "сделка закрыта"
    if report.sla_severity == "critical":
        return f"КРИТИЧНО · {report.sla_rule_label or 'stage-specific SLA'}"
    if report.sla_severity == "attention":
        return f"ВНИМАНИЕ · {report.sla_rule_label or 'stage-specific SLA'}"
    return "активного SLA-сигнала нет: стадия вне измеряемых правил или ниже порога внимания"


def _activity_line(
    activity: DealActivity | None,
    *,
    timezone_name: str,
    include_subject: bool,
) -> str:
    if activity is None:
        return "не найдена"
    parts = [activity.activity_type]
    if include_subject and activity.subject != "—":
        parts.append(activity.subject)
    if activity.deadline is not None:
        parts.append(f"срок {_format_dt(activity.deadline, timezone_name)}")
    elif activity.event_at is not None:
        parts.append(_format_dt(activity.event_at, timezone_name))
    parts.append(f"отв. {_employee_placeholder(activity.responsible_id)}")
    return " | ".join(parts)


def _employee_placeholder(user_id: str) -> str:
    return f"ID {user_id}"


def _format_activity(
    activity: DealActivity | None,
    directory: RopDirectory,
    *,
    timezone_name: str,
    include_subject: bool,
) -> str:
    if activity is None:
        return "не найдена"
    parts = [activity.activity_type]
    if include_subject and activity.subject != "—":
        parts.append(activity.subject)
    if activity.deadline is not None:
        parts.append(f"срок {_format_dt(activity.deadline, timezone_name)}")
    elif activity.event_at is not None:
        parts.append(_format_dt(activity.event_at, timezone_name))
    parts.append(f"отв. {_employee(directory, activity.responsible_id, include_id=False)}")
    return " | ".join(parts)


def _action_items(report: DealDrilldown, reference: datetime) -> list[str]:
    actions: list[str] = []
    if report.sla_severity == "critical":
        actions.append("Разобрать с ответственным причину выхода за stage-specific SLA.")
    elif report.sla_severity == "attention":
        actions.append("Проверить следующий шаг до перехода карточки в критическую зону SLA.")

    next_activity = report.next_open_activity
    if next_activity is None:
        actions.append("В локальном срезе нет незавершённой активности — назначить следующий шаг.")
    elif next_activity.deadline is not None and next_activity.deadline < reference:
        overdue_days = max(0, int((reference - next_activity.deadline).total_seconds() // 86400))
        actions.append(f"Разобрать просроченную активность: просрочка {overdue_days} дн.")

    if report.activities_count == 0:
        actions.append("Проверить ведение карточки: связанных CRM-активностей в локальном срезе нет.")
    return actions[:3]


def format_deal_drilldown(
    report: DealDrilldown,
    *,
    timezone_name: str = "Europe/Moscow",
    now: datetime | None = None,
) -> str:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    lines = [
        f"ИИ-РОП · сделка #{report.deal_id}",
        f"• Название: {report.title}",
        f"• Воронка / стадия: {category_label(report.category_id)} · {stage_label(report.stage_id)}",
        f"• Сумма: {_money(report.opportunity)} {report.currency}",
        f"• Ответственный: {_employee(report.directory, report.assigned_by_id)}",
        f"• Создана: {_format_dt(report.created_at, timezone_name)}",
        f"• На текущей стадии: {report.stage_age_days if report.stage_age_days is not None else '—'} дн.",
        f"• SLA: {_sla_text(report)}",
        f"• Связанных CRM-активностей в локальном срезе: {report.activities_count}",
    ]

    lines.extend(
        [
            "\nАктивности:",
            "• Последняя завершённая: "
            + _format_activity(
                report.last_completed_activity,
                report.directory,
                timezone_name=timezone_name,
                include_subject=True,
            ),
            "• Ближайшая незавершённая: "
            + _format_activity(
                report.next_open_activity,
                report.directory,
                timezone_name=timezone_name,
                include_subject=True,
            ),
        ]
    )

    lines.append("\nДвижение по стадиям:")
    if not report.stage_history:
        lines.append("• локальная история стадий не найдена")
    else:
        for event in report.stage_history[-8:]:
            lines.append(
                f"• {_format_dt(event.occurred_at, timezone_name)} · {stage_label(event.stage_id)}"
            )

    lines.append("\nПоследние комментарии timeline:")
    if report.timeline_error:
        lines.append(f"• точечное чтение недоступно: {report.timeline_error}")
    elif not report.timeline_comments:
        lines.append("• комментарии не найдены")
    else:
        for comment in report.timeline_comments[:5]:
            author = _employee(report.directory, comment.author_id, include_id=False)
            lines.append(
                f"• {_format_dt(comment.created_at, timezone_name)} · {author}: {comment.text}"
            )

    actions = _action_items(report, reference)
    lines.append("\nЧто проверить сегодня:")
    if not actions:
        lines.append("• отдельного детерминированного сигнала для вмешательства нет")
    else:
        lines.extend(f"• {item}" for item in actions)

    lines.append(
        "\nСделка, активности и история стадий прочитаны из локальной синхронизированной "
        "CRM. Комментарии timeline читаются точечно read-only и не сохраняются в SQLite. "
        "Запись в Bitrix24 не выполняется."
    )
    return "\n".join(lines)


def format_deal_for_ai(
    report: DealDrilldown,
    *,
    timezone_name: str = "Europe/Moscow",
) -> str:
    """Compact facts for the LLM without raw comment or activity-description text."""

    lines = [
        f"ИИ-РОП · compact deal facts #{report.deal_id}",
        f"Воронка/стадия: {category_label(report.category_id)} · {stage_label(report.stage_id)}",
        f"Сумма: {_money(report.opportunity)} {report.currency}",
        f"Ответственный: {_employee(report.directory, report.assigned_by_id)}",
        f"На текущей стадии: {report.stage_age_days if report.stage_age_days is not None else '—'} дн.",
        f"SLA: {_sla_text(report)}",
        f"CRM-активностей: {report.activities_count}",
    ]
    if report.last_completed_activity is not None:
        last = report.last_completed_activity
        lines.append(
            "Последняя завершённая активность: "
            f"{last.activity_type}; {_format_dt(last.event_at, timezone_name)}"
        )
    else:
        lines.append("Последняя завершённая активность: не найдена")

    if report.next_open_activity is not None:
        next_activity = report.next_open_activity
        lines.append(
            "Ближайшая незавершённая активность: "
            f"{next_activity.activity_type}; срок {_format_dt(next_activity.deadline, timezone_name)}"
        )
    else:
        lines.append("Ближайшая незавершённая активность: не найдена")

    if report.stage_history:
        path = " → ".join(stage_label(item.stage_id) for item in report.stage_history[-6:])
        lines.append(f"Последние стадии: {path}")

    lines.append(
        "Тексты комментариев, описаний активностей, контакты клиента и другие сырые поля "
        "не включены в AI-tool. Не утверждай причину зависания сделки без фактических данных."
    )
    return "\n".join(lines)
