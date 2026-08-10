from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite

from app.config import Settings
from app.integrations.bitrix24 import Bitrix24RequestError
from app.integrations.bitrix24.client import Bitrix24ReadOnlyClient
from app.proxy import build_proxy_url
from app.services.rop_directory import RopDirectory, employee_label, load_rop_directory
from app.storage.crm_store import CrmStore

_COMMUNICATION_TYPE_IDS = frozenset({"1", "2", "4"})
_ACTIVITY_TYPE_LABELS = {
    "0": "Активность",
    "1": "Встреча",
    "2": "Звонок",
    "3": "Задача",
    "4": "E-mail",
    "5": "Действие",
    "6": "Пользовательское действие",
}
_TECHNICAL_FUTURE_YEAR = 2099


@dataclass(frozen=True, slots=True)
class LeadStatusStat:
    status_id: str
    label: str
    semantic: str
    count: int


@dataclass(frozen=True, slots=True)
class LeadSourceStat:
    source_id: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class LeadManagerStat:
    assigned_by_id: str
    new_leads: int
    current_active: int
    successful_finalizations: int
    failed_finalizations: int
    attention_3d: int
    critical_5d: int


@dataclass(frozen=True, slots=True)
class LeadIntelligenceReport:
    days: int
    start_at: datetime
    end_at: datetime
    total_leads: int
    new_leads: int
    current_active: int
    current_success: int
    current_failed: int
    leads_with_status_events: int
    status_events: int
    successful_finalizations: int
    failed_finalizations: int
    active_attention_3d: int
    active_critical_5d: int
    current_statuses: tuple[LeadStatusStat, ...]
    status_entries: tuple[LeadStatusStat, ...]
    final_statuses: tuple[LeadStatusStat, ...]
    new_sources: tuple[LeadSourceStat, ...]
    crm_activities: int
    completed_activities: int
    activity_type_counts: tuple[tuple[str, int], ...]
    completed_communications: int
    communication_type_counts: tuple[tuple[str, int], ...]
    managers: tuple[LeadManagerStat, ...]
    catalog_loaded: bool
    history_schema_ready: bool


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


def _text(value: Any, default: str = "—") -> str:
    if value in (None, ""):
        return default
    return str(value)


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
    return _text(item.get("TYPE_ID"), "0")


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


def _status_semantic(
    item: dict[str, Any],
    status_semantics: dict[str, str],
) -> str:
    direct = _text(item.get("STATUS_SEMANTIC_ID"), "").upper()
    if direct in {"S", "F", "P"}:
        return direct
    status_id = _text(item.get("STATUS_ID"), "")
    catalog = status_semantics.get(status_id, "").upper()
    return catalog if catalog in {"S", "F", "P"} else "P"


def _history_status_id(item: dict[str, Any]) -> str:
    status_id = _text(item.get("STATUS_ID"), "")
    if status_id:
        return status_id
    return _text(item.get("STAGE_ID"), "")


def _history_semantic(
    item: dict[str, Any],
    status_semantics: dict[str, str],
) -> str:
    for key in ("STATUS_SEMANTIC_ID", "STAGE_SEMANTIC_ID"):
        semantic = _text(item.get(key), "").upper()
        if semantic in {"S", "F", "P"}:
            return semantic
    catalog = status_semantics.get(_history_status_id(item), "").upper()
    return catalog if catalog in {"S", "F", "P"} else "P"


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _format_dt(value: datetime, timezone_name: str) -> str:
    return value.astimezone(_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M")


def _build_catalog_client(settings: Settings) -> Bitrix24ReadOnlyClient:
    return Bitrix24ReadOnlyClient(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
    )


async def _load_status_catalog(
    settings: Settings,
) -> tuple[dict[str, str], dict[str, str], dict[str, str], bool]:
    if not settings.bitrix24_configured:
        return {}, {}, {}, False

    client = _build_catalog_client(settings)
    try:
        statuses = await client.call_all(
            "crm.status.list",
            {
                "filter": {"ENTITY_ID": "STATUS"},
                "order": {"SORT": "ASC"},
            },
        )
        sources = await client.call_all(
            "crm.status.list",
            {
                "filter": {"ENTITY_ID": "SOURCE"},
                "order": {"SORT": "ASC"},
            },
        )
    except Bitrix24RequestError:
        return {}, {}, {}, False

    status_labels: dict[str, str] = {}
    status_semantics: dict[str, str] = {}
    for item in statuses:
        status_id = _text(item.get("STATUS_ID"), "")
        if not status_id:
            continue
        status_labels[status_id] = _text(item.get("NAME"), status_id)
        status_semantics[status_id] = _text(item.get("SEMANTICS"), "P").upper()

    source_labels = {
        _text(item.get("STATUS_ID"), ""): _text(
            item.get("NAME"),
            _text(item.get("STATUS_ID"), "—"),
        )
        for item in sources
        if _text(item.get("STATUS_ID"), "")
    }
    return status_labels, status_semantics, source_labels, True


def _last_lead_movement(
    lead: dict[str, Any],
    history_by_owner: dict[str, list[dict[str, Any]]],
) -> datetime | None:
    lead_id = _text(lead.get("ID"), "")
    history_times = [
        value
        for value in (
            _datetime(item.get("CREATED_TIME"))
            for item in history_by_owner.get(lead_id, [])
        )
        if value is not None
    ]
    if history_times:
        return max(history_times)
    for key in ("DATE_MODIFY", "DATE_CREATE"):
        parsed = _datetime(lead.get(key))
        if parsed is not None:
            return parsed
    return None


def _manager_sort_key(item: LeadManagerStat) -> tuple[int, int, int, int, int]:
    return (
        item.critical_5d,
        item.failed_finalizations,
        item.attention_3d,
        item.current_active,
        item.new_leads,
    )


async def build_lead_intelligence(
    settings: Settings,
    days: int = 7,
    *,
    now: datetime | None = None,
) -> LeadIntelligenceReport:
    if days < 1 or days > 365:
        raise ValueError("Lead Intelligence period must be from 1 to 365 days")

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    start_at = reference - timedelta(days=days)
    leads = await _load_payloads(settings.database_path, "lead")
    history = await _load_payloads(settings.database_path, "lead_stage_history")
    activities = await _load_lead_activities(settings.database_path)
    status_labels, status_semantics, source_labels, catalog_loaded = (
        await _load_status_catalog(settings)
    )

    history_by_owner: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in history:
        owner_id = _text(item.get("OWNER_ID"), "")
        if owner_id:
            history_by_owner[owner_id].append(item)

    new_leads = 0
    current_active = 0
    current_success = 0
    current_failed = 0
    active_attention = 0
    active_critical = 0
    current_status_counts: Counter[tuple[str, str]] = Counter()
    new_source_counts: Counter[str] = Counter()
    manager_counters: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {
            "new": 0,
            "active": 0,
            "success": 0,
            "failed": 0,
            "attention": 0,
            "critical": 0,
        }
    )
    lead_by_id = {
        _text(lead.get("ID"), ""): lead
        for lead in leads
        if _text(lead.get("ID"), "")
    }

    for lead in leads:
        status_id = _text(lead.get("STATUS_ID"), "не указан")
        semantic = _status_semantic(lead, status_semantics)
        current_status_counts[(status_id, semantic)] += 1
        manager_id = _text(lead.get("ASSIGNED_BY_ID"), "не назначен")
        manager = manager_counters[manager_id]

        created_at = _datetime(lead.get("DATE_CREATE"))
        if created_at is not None and start_at <= created_at <= reference:
            new_leads += 1
            manager["new"] += 1
            new_source_counts[_text(lead.get("SOURCE_ID"), "не указан")] += 1

        if semantic == "S":
            current_success += 1
            continue
        if semantic == "F":
            current_failed += 1
            continue

        current_active += 1
        manager["active"] += 1
        movement_at = _last_lead_movement(lead, history_by_owner)
        if movement_at is None:
            idle_days = settings.rop_critical_days
        else:
            idle_days = max(
                0,
                int((reference - movement_at).total_seconds() // 86400),
            )
        if idle_days >= settings.rop_attention_days:
            active_attention += 1
            manager["attention"] += 1
        if idle_days >= settings.rop_critical_days:
            active_critical += 1
            manager["critical"] += 1

    events_in_window = [
        item
        for item in history
        if (
            (event_at := _datetime(item.get("CREATED_TIME"))) is not None
            and start_at <= event_at <= reference
        )
    ]
    history_schema_ready = all(
        item.get("STATUS_ID") not in (None, "")
        and item.get("STATUS_SEMANTIC_ID") not in (None, "")
        for item in events_in_window
    )
    leads_with_status_events = {
        _text(item.get("OWNER_ID"), "")
        for item in events_in_window
        if _text(item.get("OWNER_ID"), "")
    }

    status_entry_counts: Counter[tuple[str, str]] = Counter()
    latest_final_by_owner: dict[str, dict[str, Any]] = {}
    for item in events_in_window:
        owner_id = _text(item.get("OWNER_ID"), "")
        status_id = _history_status_id(item)
        semantic = _history_semantic(item, status_semantics)
        if status_id:
            status_entry_counts[(status_id, semantic)] += 1
        if not owner_id or semantic not in {"S", "F"}:
            continue
        previous = latest_final_by_owner.get(owner_id)
        previous_at = _datetime(previous.get("CREATED_TIME")) if previous else None
        current_at = _datetime(item.get("CREATED_TIME"))
        if previous is None or previous_at is None or (
            current_at is not None and current_at >= previous_at
        ):
            latest_final_by_owner[owner_id] = item

    successful_owners = {
        owner_id
        for owner_id, item in latest_final_by_owner.items()
        if _history_semantic(item, status_semantics) == "S"
    }
    failed_owners = {
        owner_id
        for owner_id, item in latest_final_by_owner.items()
        if _history_semantic(item, status_semantics) == "F"
    }

    final_status_counts: Counter[tuple[str, str]] = Counter()
    for owner_id, item in latest_final_by_owner.items():
        status_id = _history_status_id(item)
        semantic = _history_semantic(item, status_semantics)
        if status_id:
            final_status_counts[(status_id, semantic)] += 1
        lead = lead_by_id.get(owner_id)
        manager_id = (
            _text(lead.get("ASSIGNED_BY_ID"), "не назначен")
            if lead
            else "не назначен"
        )
        if semantic == "S":
            manager_counters[manager_id]["success"] += 1
        elif semantic == "F":
            manager_counters[manager_id]["failed"] += 1

    activity_type_counts: Counter[str] = Counter()
    communication_type_counts: Counter[str] = Counter()
    crm_activities = 0
    completed_activities = 0
    completed_communications = 0
    for item in activities:
        completed = _is_completed(item.get("COMPLETED"))
        event_at = _activity_timestamp(item, completed=completed)
        if event_at is None or not start_at <= event_at <= reference:
            continue
        crm_activities += 1
        type_id = _activity_type_id(item)
        label = _activity_type_label(type_id)
        activity_type_counts[label] += 1
        if completed:
            completed_activities += 1
        if completed and type_id in _COMMUNICATION_TYPE_IDS:
            completed_communications += 1
            communication_type_counts[label] += 1

    current_statuses = tuple(
        LeadStatusStat(
            status_id=status_id,
            label=status_labels.get(status_id, status_id),
            semantic=semantic,
            count=count,
        )
        for (status_id, semantic), count in current_status_counts.most_common()
    )
    status_entries = tuple(
        LeadStatusStat(
            status_id=status_id,
            label=status_labels.get(status_id, status_id),
            semantic=semantic,
            count=count,
        )
        for (status_id, semantic), count in status_entry_counts.most_common()
    )
    final_statuses = tuple(
        LeadStatusStat(
            status_id=status_id,
            label=status_labels.get(status_id, status_id),
            semantic=semantic,
            count=count,
        )
        for (status_id, semantic), count in final_status_counts.most_common()
    )
    new_sources = tuple(
        LeadSourceStat(
            source_id=source_id,
            label=source_labels.get(source_id, source_id),
            count=count,
        )
        for source_id, count in new_source_counts.most_common()
    )
    managers = tuple(
        sorted(
            (
                LeadManagerStat(
                    assigned_by_id=manager_id,
                    new_leads=value["new"],
                    current_active=value["active"],
                    successful_finalizations=value["success"],
                    failed_finalizations=value["failed"],
                    attention_3d=value["attention"],
                    critical_5d=value["critical"],
                )
                for manager_id, value in manager_counters.items()
            ),
            key=_manager_sort_key,
            reverse=True,
        )
    )

    return LeadIntelligenceReport(
        days=days,
        start_at=start_at,
        end_at=reference,
        total_leads=len(leads),
        new_leads=new_leads,
        current_active=current_active,
        current_success=current_success,
        current_failed=current_failed,
        leads_with_status_events=len(leads_with_status_events),
        status_events=len(events_in_window),
        successful_finalizations=len(successful_owners),
        failed_finalizations=len(failed_owners),
        active_attention_3d=active_attention,
        active_critical_5d=active_critical,
        current_statuses=current_statuses,
        status_entries=status_entries,
        final_statuses=final_statuses,
        new_sources=new_sources,
        crm_activities=crm_activities,
        completed_activities=completed_activities,
        activity_type_counts=tuple(activity_type_counts.most_common()),
        completed_communications=completed_communications,
        communication_type_counts=tuple(communication_type_counts.most_common()),
        managers=managers,
        catalog_loaded=catalog_loaded,
        history_schema_ready=history_schema_ready,
    )


def _finalized_share(report: LeadIntelligenceReport) -> str:
    total = report.successful_finalizations + report.failed_finalizations
    if total <= 0:
        return "нет финализированных переходов"
    share = 100 * report.successful_finalizations / total
    return f"{share:.1f}% (n={total})"


def _status_line(item: LeadStatusStat) -> str:
    semantic = {
        "S": "успешный финальный",
        "F": "неуспешный финальный",
        "P": "активный",
    }.get(item.semantic, item.semantic)
    return f"• {item.label} ({item.status_id}) · {semantic}: {item.count}"


def _manager_line(
    item: LeadManagerStat,
    directory: RopDirectory,
) -> str:
    return (
        f"• {employee_label(directory, item.assigned_by_id)} | новых {item.new_leads} | "
        f"активных сейчас {item.current_active} | финал S/F "
        f"{item.successful_finalizations}/{item.failed_finalizations} | "
        f"aging 3+ {item.attention_3d} / 5+ {item.critical_5d}"
    )


def format_lead_intelligence(
    report: LeadIntelligenceReport,
    directory: RopDirectory,
    *,
    timezone_name: str = "Europe/Moscow",
    manager_limit: int = 8,
) -> str:
    lines = [
        f"ИИ-РОП · Lead Intelligence · rolling {report.days} дн.",
        "• Окно: "
        f"{_format_dt(report.start_at, timezone_name)} — "
        f"{_format_dt(report.end_at, timezone_name)}",
        f"• Новых лидов: {report.new_leads}",
        f"• Всего лидов в локальной CRM: {report.total_leads}",
        f"• Активных сейчас: {report.current_active}",
        f"• Сейчас в успешном финальном статусе: {report.current_success}",
        f"• Сейчас в неуспешном финальном статусе: {report.current_failed}",
        "\nДвижение статусов за окно:",
        f"• Лидов с событиями статуса: {report.leads_with_status_events}",
        f"• Событий статуса: {report.status_events}",
    ]
    if report.history_schema_ready:
        lines.extend(
            [
                f"• Успешных финализаций: {report.successful_finalizations}",
                f"• Неуспешных финализаций: {report.failed_finalizations}",
                "• Доля успешных среди финализированных переходов: "
                f"{_finalized_share(report)}",
            ]
        )
    else:
        lines.extend(
            [
                "• Финализации S/F: временно не считаются достоверными — локальная "
                "lead_stage_history сохранена в старом формате без STATUS_*;",
                "• Выполните /bitrix_sync_incremental на версии 0.4.13+: история лидов "
                "будет автоматически перечитана read-only один раз.",
            ]
        )

    if report.history_schema_ready and report.status_entries:
        lines.append("\nВходы в статусы за окно:")
        lines.extend(_status_line(item) for item in report.status_entries[:10])

    if report.history_schema_ready and report.final_statuses:
        lines.append("\nФинализации по статусам за окно:")
        lines.extend(_status_line(item) for item in report.final_statuses[:10])

    lines.extend(
        [
            "\nAging активных лидов:",
            f"• без движения ≥3 дней: {report.active_attention_3d}",
            f"• без движения ≥5 дней: {report.active_critical_5d}",
        ]
    )

    if report.current_statuses:
        lines.append("\nТекущие статусы лидов:")
        lines.extend(_status_line(item) for item in report.current_statuses[:10])

    if report.new_sources:
        lines.append("\nИсточники новых лидов:")
        for item in report.new_sources[:10]:
            lines.append(f"• {item.label} ({item.source_id}): {item.count}")

    lines.extend(
        [
            "\nCRM-активности, привязанные к лидам, за окно:",
            f"• всего: {report.crm_activities}",
            f"• завершённых: {report.completed_activities}",
            f"• подтверждённых коммуникаций: {report.completed_communications}",
        ]
    )
    if report.activity_type_counts:
        activity_types = ", ".join(
            f"{label} {count}" for label, count in report.activity_type_counts
        )
        lines.append(f"• по типам: {activity_types}")
    if report.communication_type_counts:
        communication_types = ", ".join(
            f"{label} {count}" for label, count in report.communication_type_counts
        )
        lines.append(f"• коммуникации по типам: {communication_types}")

    if report.managers:
        lines.append("\nМенеджеры · текущая операционная картина:")
        lines.extend(
            _manager_line(item, directory)
            for item in report.managers[:manager_limit]
        )
        unresolved = sum(
            1
            for item in report.managers[:manager_limit]
            if item.assigned_by_id not in directory.users
            and item.assigned_by_id != "не назначен"
        )
        if unresolved:
            lines.append(
                f"• Справочник сотрудников не разрешил {unresolved} из показанных "
                "manager ID. Начиная с 0.4.13 обычный Bitrix sync обновляет directory "
                "автоматически."
            )

    lines.extend(
        [
            "\nМетодология:",
            "• успешная/неуспешная финализация считается по lead_stage_history "
            "(STATUS_SEMANTIC_ID S/F), а не как созданная сделка;",
            "• если один лид несколько раз попал в финальный статус в окне, для итогового "
            "S/F берётся его последнее финальное событие;",
            "• доля успешных среди финализированных — не lead→deal cohort conversion;",
            "• менеджер финализации определяется по текущему ASSIGNED_BY_ID лида, "
            "поскольку stage history не хранит исторического ответственного;",
            "• aging 3+/5+ — общий сигнал движения, не stage-specific SLA лида;",
            "• first-response SLA здесь не рассчитывается;",
            "• тексты писем, телефоны, e-mail и другие контакты клиента в отчёт не входят.",
        ]
    )
    if not report.catalog_loaded:
        lines.append(
            "• справочник названий статусов/источников Bitrix24 сейчас недоступен; "
            "для них показаны CRM ID."
        )
    return "\n".join(lines)


async def build_and_format_lead_intelligence(
    settings: Settings,
    days: int = 7,
) -> str:
    report = await build_lead_intelligence(settings, days)
    directory = await load_rop_directory(settings.database_path)
    return format_lead_intelligence(
        report,
        directory,
        timezone_name=settings.rop_timezone,
    )


def format_lead_intelligence_for_ai(
    report: LeadIntelligenceReport,
    directory: RopDirectory,
    *,
    timezone_name: str = "Europe/Moscow",
) -> str:
    text = format_lead_intelligence(
        report,
        directory,
        timezone_name=timezone_name,
    )
    return (
        f"{text}\n"
        "AI guardrail: не дели новые сделки на новые лиды и не называй это "
        "lead→deal conversion. Не называй менеджера худшим без явной метрики; "
        "формулируй, по какому показателю его профиль наиболее тревожный. "
        "Если history_schema не готова, не трактуй S/F=0 как бизнес-факт. "
        "Этот tool возвращает агрегаты, а не список lead ID: не обещай выгрузить список "
        "лидов, карточки или manager-specific export без отдельного доступного tool."
    )
