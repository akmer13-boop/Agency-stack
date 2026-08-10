from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from app.config import Settings
from app.services.rop_directory import RopDirectory, employee_label, load_rop_directory
from app.storage.crm_store import CrmStore

_COMMUNICATION_TYPE_IDS = frozenset({"1", "2", "4"})
_TECHNICAL_FUTURE_YEAR = 2099


@dataclass(frozen=True, slots=True)
class WeekendManagerStat:
    assigned_by_id: str
    leads: int
    leads_with_activity: int
    leads_with_communication: int
    completed_communications: int
    current_active: int
    current_success: int
    current_failed: int
    median_first_communication_seconds: float | None


@dataclass(frozen=True, slots=True)
class WeekendLeadReport:
    start_at: datetime
    end_at: datetime
    observed_until: datetime
    total_leads: int
    leads_with_activity: int
    leads_with_communication: int
    completed_communications: int
    current_active: int
    current_success: int
    current_failed: int
    median_first_communication_seconds: float | None
    managers: tuple[WeekendManagerStat, ...]


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed.year >= _TECHNICAL_FUTURE_YEAR:
        return None
    return parsed


def _text(value: Any, default: str = "—") -> str:
    if value in (None, ""):
        return default
    return str(value)


def _is_completed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "y", "yes"}


def _activity_timestamp(item: dict[str, Any], *, completed: bool) -> datetime | None:
    if completed:
        keys = ("END_TIME", "START_TIME", "LAST_UPDATED", "CREATED", "DEADLINE")
    else:
        keys = ("LAST_UPDATED", "CREATED", "START_TIME", "END_TIME", "DEADLINE")
    for key in keys:
        parsed = _datetime(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _lead_semantic(item: dict[str, Any]) -> str:
    semantic = _text(item.get("STATUS_SEMANTIC_ID"), "P").upper()
    return semantic if semantic in {"P", "S", "F"} else "P"


def _resolve_weekend_window(
    timezone_name: str,
    reference: datetime,
) -> tuple[datetime, datetime]:
    zone = _timezone(timezone_name)
    local_reference = reference.astimezone(zone)
    weekday = local_reference.weekday()

    if weekday >= 5:
        saturday = local_reference.date() - timedelta(days=weekday - 5)
    else:
        saturday = local_reference.date() - timedelta(days=weekday + 2)

    start_local = datetime.combine(saturday, time.min, tzinfo=zone)
    scheduled_end = start_local + timedelta(days=2)
    end_local = min(scheduled_end, local_reference)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def _load_payloads(database_path: str, entity_type: str) -> list[dict[str, Any]]:
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


async def _load_lead_activities(database_path: str) -> list[dict[str, Any]]:
    store = CrmStore(database_path)
    await store.initialize()
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT payload_json
            FROM crm_raw_entities
            WHERE entity_type = 'activity'
              AND CAST(json_extract(payload_json, '$.OWNER_TYPE_ID') AS INTEGER) = 1
            ORDER BY CAST(entity_id AS INTEGER)
            """
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


async def build_weekend_lead_report(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> WeekendLeadReport:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    start_at, end_at = _resolve_weekend_window(settings.rop_timezone, reference)
    leads = await _load_payloads(settings.database_path, "lead")
    activities = await _load_lead_activities(settings.database_path)

    cohort: dict[str, tuple[dict[str, Any], datetime]] = {}
    for lead in leads:
        lead_id = _text(lead.get("ID"), "")
        created_at = _datetime(lead.get("DATE_CREATE"))
        if lead_id and created_at is not None and start_at <= created_at < end_at:
            cohort[lead_id] = (lead, created_at)

    activities_by_lead: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in activities:
        owner_id = _text(item.get("OWNER_ID"), "")
        if owner_id in cohort:
            activities_by_lead[owner_id].append(item)

    manager_rows: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "leads": 0,
            "activity": 0,
            "communication_leads": 0,
            "communications": 0,
            "active": 0,
            "success": 0,
            "failed": 0,
            "delays": [],
        }
    )

    leads_with_activity = 0
    leads_with_communication = 0
    completed_communications = 0
    current_active = 0
    current_success = 0
    current_failed = 0
    first_communication_delays: list[float] = []

    for lead_id, (lead, created_at) in cohort.items():
        manager_id = _text(lead.get("ASSIGNED_BY_ID"), "не назначен")
        manager = manager_rows[manager_id]
        manager["leads"] += 1

        semantic = _lead_semantic(lead)
        if semantic == "S":
            current_success += 1
            manager["success"] += 1
        elif semantic == "F":
            current_failed += 1
            manager["failed"] += 1
        else:
            current_active += 1
            manager["active"] += 1

        valid_activities: list[tuple[dict[str, Any], datetime, bool]] = []
        for item in activities_by_lead.get(lead_id, []):
            completed = _is_completed(item.get("COMPLETED"))
            event_at = _activity_timestamp(item, completed=completed)
            if event_at is None or event_at < created_at or event_at > reference:
                continue
            valid_activities.append((item, event_at, completed))

        if valid_activities:
            leads_with_activity += 1
            manager["activity"] += 1

        communications = [
            (event_at, item)
            for item, event_at, completed in valid_activities
            if completed and _text(item.get("TYPE_ID"), "0") in _COMMUNICATION_TYPE_IDS
        ]
        if communications:
            leads_with_communication += 1
            manager["communication_leads"] += 1
            completed_communications += len(communications)
            manager["communications"] += len(communications)
            first_at = min(event_at for event_at, _item in communications)
            delay = max(0.0, (first_at - created_at).total_seconds())
            first_communication_delays.append(delay)
            manager["delays"].append(delay)

    managers = tuple(
        sorted(
            (
                WeekendManagerStat(
                    assigned_by_id=manager_id,
                    leads=int(values["leads"]),
                    leads_with_activity=int(values["activity"]),
                    leads_with_communication=int(values["communication_leads"]),
                    completed_communications=int(values["communications"]),
                    current_active=int(values["active"]),
                    current_success=int(values["success"]),
                    current_failed=int(values["failed"]),
                    median_first_communication_seconds=(
                        float(median(values["delays"])) if values["delays"] else None
                    ),
                )
                for manager_id, values in manager_rows.items()
            ),
            key=lambda item: (item.leads, item.leads_with_communication),
            reverse=True,
        )
    )

    return WeekendLeadReport(
        start_at=start_at,
        end_at=end_at,
        observed_until=reference,
        total_leads=len(cohort),
        leads_with_activity=leads_with_activity,
        leads_with_communication=leads_with_communication,
        completed_communications=completed_communications,
        current_active=current_active,
        current_success=current_success,
        current_failed=current_failed,
        median_first_communication_seconds=(
            float(median(first_communication_delays)) if first_communication_delays else None
        ),
        managers=managers,
    )


def _percent(part: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{100 * part / total:.1f}%"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "нет подтверждённых коммуникаций"
    total_minutes = int(round(seconds / 60))
    if total_minutes < 60:
        return f"{total_minutes} мин"
    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч"


def _format_dt(value: datetime, timezone_name: str) -> str:
    return value.astimezone(_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")


def _manager_line(item: WeekendManagerStat, directory: RopDirectory) -> str:
    no_communication = item.leads - item.leads_with_communication
    return (
        f"• {employee_label(directory, item.assigned_by_id)} | лидов {item.leads} | "
        f"с CRM-активностью {item.leads_with_activity}/{item.leads} "
        f"({_percent(item.leads_with_activity, item.leads)}) | "
        f"с подтверждённой коммуникацией {item.leads_with_communication}/{item.leads} "
        f"({_percent(item.leads_with_communication, item.leads)}) | "
        f"без подтверждённой коммуникации {no_communication} | "
        f"коммуникаций {item.completed_communications} | "
        f"медиана до первой подтверждённой коммуникации "
        f"{_format_duration(item.median_first_communication_seconds)} | "
        f"сейчас P/S/F {item.current_active}/{item.current_success}/{item.current_failed}"
    )


def format_weekend_lead_report(
    report: WeekendLeadReport,
    directory: RopDirectory,
    *,
    timezone_name: str,
    manager_limit: int = 12,
) -> str:
    no_communication = report.total_leads - report.leads_with_communication
    lines = [
        "ИИ-РОП · Лиды за выходные",
        "• Период поступления лидов: "
        f"{_format_dt(report.start_at, timezone_name)} — "
        f"{_format_dt(report.end_at, timezone_name)} ({timezone_name})",
        "• Обработка проверена с момента создания каждого лида по "
        f"{_format_dt(report.observed_until, timezone_name)}",
        f"• Пришло лидов: {report.total_leads}",
        f"• С любой CRM-активностью после создания: {report.leads_with_activity} "
        f"({_percent(report.leads_with_activity, report.total_leads)})",
        f"• С подтверждённой коммуникацией после создания: "
        f"{report.leads_with_communication} "
        f"({_percent(report.leads_with_communication, report.total_leads)})",
        f"• Без подтверждённой коммуникации: {no_communication}",
        f"• Завершённых подтверждённых коммуникаций: {report.completed_communications}",
        "• Медиана до первой подтверждённой CRM-коммуникации: "
        f"{_format_duration(report.median_first_communication_seconds)} "
        f"(n={report.leads_with_communication})",
        "• Текущий статус этой когорты P/S/F: "
        f"{report.current_active}/{report.current_success}/{report.current_failed}",
    ]

    if report.managers:
        lines.append("\nМенеджеры · обработка лидов этой weekend-когорты:")
        lines.extend(
            _manager_line(item, directory) for item in report.managers[:manager_limit]
        )

    lines.extend(
        [
            "\nМетодология:",
            "• 'за выходные' = календарные суббота+воскресенье в ROP_TIMEZONE; "
            "если запрос сделан в сами выходные, окно заканчивается текущим моментом;",
            "• менеджер = текущий ASSIGNED_BY_ID; это не доказывает, кто был ответственным "
            "в момент поступления лида, если ответственный позже менялся;",
            "• подтверждённая коммуникация = завершённая CRM activity типа встреча, звонок "
            "или e-mail; пользовательские действия и неизвестные типы сюда не входят;",
            "• медиана до первой подтверждённой коммуникации — наблюдаемый CRM-факт, "
            "а не first-response SLA и не гарантия времени первого ответа клиенту;",
            "• отчёт не передаёт в LLM названия лидов, телефоны, e-mail клиента или сырые "
            "тексты активностей.",
        ]
    )
    return "\n".join(lines)


async def build_and_format_weekend_leads(settings: Settings) -> str:
    report = await build_weekend_lead_report(settings)
    directory = await load_rop_directory(settings.database_path)
    return format_weekend_lead_report(
        report,
        directory,
        timezone_name=settings.rop_timezone,
    )
