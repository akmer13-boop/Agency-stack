from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
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


@dataclass(frozen=True, slots=True)
class EvidenceActivity:
    activity_type_id: str
    activity_type: str
    completed: bool
    event_at: datetime


@dataclass(frozen=True, slots=True)
class DealStageEvidence:
    deal_id: str
    stage_id: str
    stage_entered_at: datetime | None
    activities_after_stage: int | None
    completed_after_stage: int | None
    completed_communications_after_stage: int | None
    activity_type_counts: tuple[tuple[str, int], ...]
    last_activity_type: str | None
    last_activity_at: datetime | None
    last_activity_completed: bool | None
    days_since_last_activity: int | None
    last_communication_type: str | None
    last_communication_at: datetime | None
    days_since_last_communication: int | None
    next_open_activity_exists: bool


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


def _stage_entered_at(report: DealDrilldown) -> datetime | None:
    current_stage_events = [
        item.occurred_at
        for item in report.stage_history
        if item.stage_id == report.stage_id
    ]
    if current_stage_events:
        return max(current_stage_events)
    return report.last_movement_at


def _days_since(reference: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((reference - value).total_seconds() // 86400))


async def build_deal_stage_evidence(
    settings: Settings,
    report: DealDrilldown,
    *,
    now: datetime | None = None,
) -> DealStageEvidence:
    reference = (now or datetime.now(UTC)).astimezone(UTC)
    stage_entered_at = _stage_entered_at(report)

    if stage_entered_at is None:
        return DealStageEvidence(
            deal_id=report.deal_id,
            stage_id=report.stage_id,
            stage_entered_at=None,
            activities_after_stage=None,
            completed_after_stage=None,
            completed_communications_after_stage=None,
            activity_type_counts=(),
            last_activity_type=None,
            last_activity_at=None,
            last_activity_completed=None,
            days_since_last_activity=None,
            last_communication_type=None,
            last_communication_at=None,
            days_since_last_communication=None,
            next_open_activity_exists=report.next_open_activity is not None,
        )

    activities: list[EvidenceActivity] = []
    for item in await _load_activity_payloads(settings.database_path, report.deal_id):
        completed = _is_completed(item.get("COMPLETED"))
        event_at = _activity_timestamp(item, completed=completed)
        if event_at is None or event_at < stage_entered_at or event_at > reference:
            continue
        type_id = _activity_type_id(item)
        activities.append(
            EvidenceActivity(
                activity_type_id=type_id,
                activity_type=_activity_type_label(type_id),
                completed=completed,
                event_at=event_at,
            )
        )

    type_counts = Counter(item.activity_type for item in activities)
    activity_type_counts = tuple(
        sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))
    )

    last_activity = max(activities, key=lambda item: item.event_at) if activities else None
    communications = [
        item
        for item in activities
        if item.completed and item.activity_type_id in _COMMUNICATION_TYPE_IDS
    ]
    last_communication = (
        max(communications, key=lambda item: item.event_at) if communications else None
    )

    return DealStageEvidence(
        deal_id=report.deal_id,
        stage_id=report.stage_id,
        stage_entered_at=stage_entered_at,
        activities_after_stage=len(activities),
        completed_after_stage=sum(item.completed for item in activities),
        completed_communications_after_stage=len(communications),
        activity_type_counts=activity_type_counts,
        last_activity_type=last_activity.activity_type if last_activity else None,
        last_activity_at=last_activity.event_at if last_activity else None,
        last_activity_completed=last_activity.completed if last_activity else None,
        days_since_last_activity=_days_since(
            reference,
            last_activity.event_at if last_activity else None,
        ),
        last_communication_type=(
            last_communication.activity_type if last_communication else None
        ),
        last_communication_at=(
            last_communication.event_at if last_communication else None
        ),
        days_since_last_communication=_days_since(
            reference,
            last_communication.event_at if last_communication else None,
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


def _type_counts_text(evidence: DealStageEvidence) -> str:
    if not evidence.activity_type_counts:
        return "нет датированных активностей"
    return ", ".join(
        f"{label} {count}" for label, count in evidence.activity_type_counts
    )


def _evidence_signals(report: DealDrilldown, evidence: DealStageEvidence) -> list[str]:
    signals: list[str] = []
    if evidence.activities_after_stage is None:
        signals.append(
            "Дата входа на текущую стадию не установлена; временную активность "
            "после входа доказательно посчитать нельзя."
        )
    elif evidence.activities_after_stage == 0:
        signals.append(
            "После входа на текущую стадию в локальном срезе нет датированных "
            "CRM-активностей."
        )
    else:
        signals.append(
            "После входа на текущую стадию CRM фиксирует "
            f"{evidence.activities_after_stage} активностей."
        )

    if evidence.completed_communications_after_stage:
        signals.append(
            "После входа на стадию были завершённые коммуникационные активности: "
            f"{evidence.completed_communications_after_stage}."
        )

    if report.sla_severity == "critical":
        signals.append("Текущая стадия остаётся критичной по stage-specific SLA.")
    elif report.sla_severity == "attention":
        signals.append("Текущая стадия находится в зоне внимания stage-specific SLA.")

    if not evidence.next_open_activity_exists:
        signals.append("Незавершённого следующего шага в CRM сейчас нет.")
    return signals


def format_deal_stage_evidence(
    report: DealDrilldown,
    evidence: DealStageEvidence,
    *,
    timezone_name: str = "Europe/Moscow",
) -> str:
    lines = [
        "ИИ-РОП · доказательная диагностика текущей стадии",
        f"• Вход на стадию: {_format_dt(evidence.stage_entered_at, timezone_name)}",
    ]

    if evidence.activities_after_stage is None:
        lines.append("• CRM-активности после входа: временное окно не установлено")
    else:
        lines.extend(
            [
                f"• CRM-активностей после входа: {evidence.activities_after_stage}",
                f"• Завершённых после входа: {evidence.completed_after_stage}",
                f"• По типам: {_type_counts_text(evidence)}",
                "• Завершённых коммуникаций после входа: "
                f"{evidence.completed_communications_after_stage}",
            ]
        )

    if evidence.last_activity_at is None:
        lines.append("• Последняя активность после входа: не найдена")
    else:
        state = "завершена" if evidence.last_activity_completed else "не завершена"
        lines.append(
            "• Последняя активность после входа: "
            f"{evidence.last_activity_type} · "
            f"{_format_dt(evidence.last_activity_at, timezone_name)} · {state}"
        )
        lines.append(
            f"• Дней с последней активности: {evidence.days_since_last_activity}"
        )

    if evidence.last_communication_at is None:
        lines.append("• Последняя завершённая коммуникация после входа: не найдена")
    else:
        lines.append(
            "• Последняя завершённая коммуникация после входа: "
            f"{evidence.last_communication_type} · "
            f"{_format_dt(evidence.last_communication_at, timezone_name)}"
        )
        lines.append(
            "• Дней с последней завершённой коммуникации: "
            f"{evidence.days_since_last_communication}"
        )

    lines.append(
        "• Следующая незавершённая активность: "
        + ("есть" if evidence.next_open_activity_exists else "отсутствует")
    )

    lines.append("\nДоказательные сигналы:")
    lines.extend(f"• {item}" for item in _evidence_signals(report, evidence))
    lines.append(
        "\nЭта диагностика использует только даты и типы CRM-активностей. "
        "Она не делает вывод о содержании писем/звонков и не подменяет ручную "
        "проверку результата коммуникации."
    )
    return "\n".join(lines)


def format_deal_stage_evidence_for_ai(
    report: DealDrilldown,
    evidence: DealStageEvidence,
    *,
    timezone_name: str = "Europe/Moscow",
) -> str:
    """Compact stage evidence for the LLM without raw communication text."""

    lines = [
        f"EVIDENCE текущей стадии сделки #{report.deal_id}",
        f"Вход на стадию: {_format_dt(evidence.stage_entered_at, timezone_name)}",
    ]
    if evidence.activities_after_stage is None:
        lines.append("Активности после входа: временное окно не установлено")
    else:
        lines.extend(
            [
                f"Активностей после входа: {evidence.activities_after_stage}",
                f"Завершённых после входа: {evidence.completed_after_stage}",
                f"Типы активностей: {_type_counts_text(evidence)}",
                "Завершённых коммуникаций после входа: "
                f"{evidence.completed_communications_after_stage}",
            ]
        )

    if evidence.last_activity_at is not None:
        lines.append(
            "Последняя активность после входа: "
            f"{evidence.last_activity_type}; "
            f"{_format_dt(evidence.last_activity_at, timezone_name)}; "
            f"дней назад {evidence.days_since_last_activity}"
        )
    else:
        lines.append("Последняя активность после входа: не найдена")

    if evidence.last_communication_at is not None:
        lines.append(
            "Последняя завершённая коммуникация после входа: "
            f"{evidence.last_communication_type}; "
            f"{_format_dt(evidence.last_communication_at, timezone_name)}; "
            f"дней назад {evidence.days_since_last_communication}"
        )
    else:
        lines.append("Последняя завершённая коммуникация после входа: не найдена")

    lines.append(
        "Следующая незавершённая активность: "
        + ("есть" if evidence.next_open_activity_exists else "отсутствует")
    )
    lines.append("Доказательные сигналы:")
    lines.extend(f"- {item}" for item in _evidence_signals(report, evidence))
    lines.extend(
        [
            "Guardrail: название SLA-правила 'Follow-up после КП' означает контроль "
            "возраста стадии и само по себе НЕ доказывает, что follow-up не выполнялся.",
            "Если после входа есть завершённые E-mail/звонки/встречи, не утверждай и не "
            "выдвигай как основную гипотезу 'follow-up не было'.",
            "Не перечисляй цену, отсутствие ЛПР, неактуальное КП и другие универсальные "
            "причины как вероятные без отдельного сигнала из tool output. Их можно назвать "
            "только пунктами ручной проверки.",
            "Сырые тексты писем, комментариев и описаний активностей в этот AI-блок не входят.",
        ]
    )
    return "\n".join(lines)
