from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from app.config import Settings
from app.services.rop_deal import DealDrilldown
from app.storage.crm_store import CrmStore

_ACTIVITY_TYPE_LABELS = {
    "0": "Активность",
    "1": "Встреча",
    "2": "Звонок",
    "3": "Задача",
    "4": "E-mail",
    "5": "Действие",
}
_COMMUNICATION_TYPE_IDS = frozenset({"1", "2", "4"})
_TECHNICAL_FUTURE_YEAR = 2099
_MIN_DAYS = 1
_MAX_DAYS = 365


@dataclass(frozen=True, slots=True)
class RecentDealActivity:
    deal_id: str
    days: int
    window_start: datetime
    window_end: datetime
    activities_count: int
    completed_count: int
    open_count: int
    completed_communications_count: int
    activity_type_counts: tuple[tuple[str, int], ...]
    communication_type_counts: tuple[tuple[str, int], ...]
    last_activity_type: str | None
    last_activity_at: datetime | None
    last_communication_type: str | None
    last_communication_at: datetime | None
    next_open_activity_exists: bool


@dataclass(frozen=True, slots=True)
class _ActivityPoint:
    activity_type_id: str
    activity_type: str
    completed: bool
    event_at: datetime


def validate_recent_activity_days(days: int) -> int:
    if days < _MIN_DAYS or days > _MAX_DAYS:
        raise ValueError(f"days must be between {_MIN_DAYS} and {_MAX_DAYS}")
    return days


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


def _is_completed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "y", "yes"}


def _activity_type_id(item: dict[str, Any]) -> str:
    value = item.get("TYPE_ID")
    return str(value) if value not in (None, "") else "0"


def _activity_type_label(type_id: str) -> str:
    return _ACTIVITY_TYPE_LABELS.get(type_id, f"Другой тип (ID {type_id})")


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


async def _load_activity_payloads(database_path: str, deal_id: str) -> list[dict[str, Any]]:
    store = CrmStore(database_path)
    await store.initialize()
    async with aiosqlite.connect(database_path) as database:
        cursor = await database.execute(
            """
            SELECT payload_json
            FROM crm_raw_entities
            WHERE entity_type = 'activity'
              AND CAST(json_extract(payload_json, '$.OWNER_ID') AS TEXT) = ?
              AND CAST(json_extract(payload_json, '$.OWNER_TYPE_ID') AS INTEGER) = 2
            ORDER BY CAST(entity_id AS INTEGER)
            """,
            (deal_id,),
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


async def build_recent_deal_activity(
    settings: Settings,
    report: DealDrilldown,
    days: int,
    *,
    now: datetime | None = None,
) -> RecentDealActivity:
    validated_days = validate_recent_activity_days(days)
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    window_start = reference - timedelta(days=validated_days)

    points: list[_ActivityPoint] = []
    for item in await _load_activity_payloads(settings.database_path, report.deal_id):
        completed = _is_completed(item.get("COMPLETED"))
        event_at = _activity_timestamp(item, completed=completed)
        if event_at is None or event_at < window_start or event_at > reference:
            continue
        type_id = _activity_type_id(item)
        points.append(
            _ActivityPoint(
                activity_type_id=type_id,
                activity_type=_activity_type_label(type_id),
                completed=completed,
                event_at=event_at,
            )
        )

    activity_counts = Counter(point.activity_type for point in points)
    communications = [
        point
        for point in points
        if point.completed and point.activity_type_id in _COMMUNICATION_TYPE_IDS
    ]
    communication_counts = Counter(point.activity_type for point in communications)
    last_activity = max(points, key=lambda point: point.event_at) if points else None
    last_communication = (
        max(communications, key=lambda point: point.event_at) if communications else None
    )

    return RecentDealActivity(
        deal_id=report.deal_id,
        days=validated_days,
        window_start=window_start,
        window_end=reference,
        activities_count=len(points),
        completed_count=sum(point.completed for point in points),
        open_count=sum(not point.completed for point in points),
        completed_communications_count=len(communications),
        activity_type_counts=tuple(
            sorted(activity_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        communication_type_counts=tuple(
            sorted(communication_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        last_activity_type=last_activity.activity_type if last_activity else None,
        last_activity_at=last_activity.event_at if last_activity else None,
        last_communication_type=(
            last_communication.activity_type if last_communication else None
        ),
        last_communication_at=(
            last_communication.event_at if last_communication else None
        ),
        next_open_activity_exists=report.next_open_activity is not None,
    )


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _format_dt(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "—"
    return value.astimezone(_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")


def _counts_text(items: tuple[tuple[str, int], ...]) -> str:
    if not items:
        return "нет"
    return ", ".join(f"{label} {count}" for label, count in items)


def format_recent_deal_activity(
    report: DealDrilldown,
    activity: RecentDealActivity,
    *,
    timezone_name: str = "Europe/Moscow",
) -> str:
    lines = [
        f"ИИ-РОП · активность сделки #{report.deal_id} за последние {activity.days} дн.",
        "Окно — скользящие последние "
        f"{activity.days}×24 ч: {_format_dt(activity.window_start, timezone_name)} → "
        f"{_format_dt(activity.window_end, timezone_name)}",
        f"• CRM-активностей: {activity.activities_count}",
        f"• Завершённых: {activity.completed_count}",
        f"• Незавершённых в окне: {activity.open_count}",
        f"• По типам: {_counts_text(activity.activity_type_counts)}",
        "• Подтверждённых коммуникаций: "
        f"{activity.completed_communications_count}",
        "• Коммуникации по типам: "
        f"{_counts_text(activity.communication_type_counts)}",
    ]

    if activity.last_activity_at is None:
        lines.append("• Последняя активность в окне: не найдена")
    else:
        lines.append(
            "• Последняя активность в окне: "
            f"{activity.last_activity_type} · "
            f"{_format_dt(activity.last_activity_at, timezone_name)}"
        )

    if activity.last_communication_at is None:
        lines.append("• Последняя завершённая коммуникация в окне: не найдена")
    else:
        lines.append(
            "• Последняя завершённая коммуникация в окне: "
            f"{activity.last_communication_type} · "
            f"{_format_dt(activity.last_communication_at, timezone_name)}"
        )

    lines.append(
        "• Текущий next action по сделке: "
        + ("есть" if activity.next_open_activity_exists else "отсутствует")
    )
    lines.append(
        "\nНеизвестные/другие типы активности считаются в общем числе, но не считаются "
        "коммуникацией без явной классификации. Сырые тексты писем здесь не анализируются."
    )
    return "\n".join(lines)


def format_recent_deal_activity_for_ai(
    report: DealDrilldown,
    activity: RecentDealActivity,
    *,
    timezone_name: str = "Europe/Moscow",
) -> str:
    lines = [
        f"RECENT ACTIVITY сделки #{report.deal_id}: последние {activity.days} дней",
        "Window start: " + _format_dt(activity.window_start, timezone_name),
        "Window end: " + _format_dt(activity.window_end, timezone_name),
        f"Activities in window: {activity.activities_count}",
        f"Completed in window: {activity.completed_count}",
        f"Open in window: {activity.open_count}",
        f"Activity types: {_counts_text(activity.activity_type_counts)}",
        "Completed communications in window: "
        f"{activity.completed_communications_count}",
        "Communication types: "
        f"{_counts_text(activity.communication_type_counts)}",
        "Last activity in window: "
        f"{activity.last_activity_type or 'none'}; "
        f"{_format_dt(activity.last_activity_at, timezone_name)}",
        "Last completed communication in window: "
        f"{activity.last_communication_type or 'none'}; "
        f"{_format_dt(activity.last_communication_at, timezone_name)}",
        "Current next open activity: "
        + ("present" if activity.next_open_activity_exists else "missing"),
        "Guardrail: unknown/other activity types are not communications unless the "
        "tool counted them in Completed communications in window.",
        "Guardrail: raw message bodies, subjects and client contacts are not included.",
    ]
    return "\n".join(lines)
