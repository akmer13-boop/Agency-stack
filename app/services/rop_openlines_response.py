from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from statistics import median
from typing import Any

import aiosqlite


@dataclass(frozen=True, slots=True)
class _DirectoryEntry:
    user_id: str
    label: str
    active: bool


@dataclass(frozen=True, slots=True)
class OpenLinesManagerResponseRow:
    manager_user_id: str
    manager_label: str
    active: bool
    response_events: int
    first_response_events: int
    response_median_seconds: float | None
    response_p90_seconds: float | None
    first_response_median_seconds: float | None
    first_response_p90_seconds: float | None


@dataclass(frozen=True, slots=True)
class OpenLinesChannelResponseRow:
    channel: str
    response_events: int
    first_response_events: int
    response_median_seconds: float | None
    response_p90_seconds: float | None


@dataclass(frozen=True, slots=True)
class OpenLinesResponseReport:
    days: int
    start_at: datetime
    end_at: datetime
    manager_filter: str | None
    manager_filter_label: str | None
    response_events: int
    first_response_events: int
    response_median_seconds: float | None
    response_p90_seconds: float | None
    first_response_median_seconds: float | None
    first_response_p90_seconds: float | None
    current_client_tail_candidates: int | None
    current_initial_no_response_candidates: int | None
    managers: tuple[OpenLinesManagerResponseRow, ...]
    channels: tuple[OpenLinesChannelResponseRow, ...]


def _p90(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, ceil(0.90 * len(ordered)))
    return float(ordered[rank - 1])


def _median(values: list[int]) -> float | None:
    return float(median(values)) if values else None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _active_value(value: Any) -> bool:
    return value not in (False, 0, "0", "N", "n", "false", "False")


def _department_ids(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


def _display_name(payload: dict[str, Any], user_id: str) -> str:
    name = str(payload.get("NAME") or "").strip()
    last_name = str(payload.get("LAST_NAME") or "").strip()
    value = " ".join(part for part in (name, last_name) if part).strip()
    return value or f"ID {user_id}"


async def _load_directory(
    database: aiosqlite.Connection,
) -> dict[str, _DirectoryEntry]:
    cursor = await database.execute(
        """
        SELECT entity_type, entity_id, payload_json
        FROM crm_active_entities
        WHERE entity_type IN ('department', 'user')
        """
    )
    rows = await cursor.fetchall()

    departments: dict[str, str] = {}
    users_payload: list[tuple[str, dict[str, Any]]] = []

    for row in rows:
        entity_type = str(row["entity_type"])
        entity_id = str(row["entity_id"])
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        if entity_type == "department":
            departments[entity_id] = str(payload.get("NAME") or f"Отдел {entity_id}").strip()
        elif entity_type == "user":
            users_payload.append((entity_id, payload))

    result: dict[str, _DirectoryEntry] = {}
    for user_id, payload in users_payload:
        department_names = [
            departments[department_id]
            for department_id in _department_ids(payload.get("UF_DEPARTMENT"))
            if department_id in departments
        ]
        label = _display_name(payload, user_id)
        if department_names:
            label = f"{label} · {' / '.join(department_names)}"
        label = f"{label} (ID {user_id})"

        result[user_id] = _DirectoryEntry(
            user_id=user_id,
            label=label,
            active=_active_value(payload.get("ACTIVE", True)),
        )

    return result


def _manager_label(
    directory: dict[str, _DirectoryEntry],
    manager_user_id: str,
) -> str:
    identity = directory.get(manager_user_id)
    return identity.label if identity is not None else f"ID {manager_user_id}"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "нет наблюдений"

    total_seconds = max(0, int(round(seconds)))
    if total_seconds < 60:
        return f"{total_seconds} сек"

    total_minutes = int(round(total_seconds / 60))
    if total_minutes < 60:
        return f"{total_minutes} мин"

    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин"

    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч"


async def _assert_required_tables(database: aiosqlite.Connection) -> None:
    required = {
        "crm_active_entities",
        "conversation_threads",
        "conversation_response_intervals",
        "conversation_thread_metrics",
        "conversation_manager_metrics",
    }
    cursor = await database.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )
    available = {str(row[0]) for row in await cursor.fetchall()}
    missing = required - available
    if missing:
        raise RuntimeError(
            "Open Lines factual layer is not materialized: " + ", ".join(sorted(missing))
        )


async def build_openlines_response_report(
    database_path: str,
    days: int = 7,
    *,
    manager_id: str | None = None,
    now: datetime | None = None,
) -> OpenLinesResponseReport:
    if days < 1 or days > 365:
        raise ValueError("Open Lines response period must be from 1 to 365 days")

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    start_at = reference - timedelta(days=days)
    manager_filter = str(manager_id) if manager_id is not None else None

    async with aiosqlite.connect(database_path) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA query_only=ON")
        await _assert_required_tables(database)
        directory = await _load_directory(database)

        manager_filter_label = (
            _manager_label(directory, manager_filter) if manager_filter is not None else None
        )

        cursor = await database.execute(
            """
            SELECT
                interval.manager_user_id,
                interval.wait_seconds,
                interval.is_first_manager_response,
                interval.to_first_sent_at,
                COALESCE(
                    NULLIF(TRIM(thread.connector_title), ''),
                    'UNKNOWN'
                ) AS channel
            FROM conversation_response_intervals AS interval
            JOIN conversation_threads AS thread
              ON thread.chat_id = interval.chat_id
            WHERE interval.transition_type = 'client_to_manager'
              AND interval.manager_user_id IS NOT NULL
            ORDER BY interval.to_first_sent_at, interval.chat_id
            """
        )
        rows = await cursor.fetchall()

        response_waits: list[int] = []
        first_response_waits: list[int] = []
        manager_waits: dict[str, list[int]] = defaultdict(list)
        manager_first_waits: dict[str, list[int]] = defaultdict(list)
        channel_waits: dict[str, list[int]] = defaultdict(list)
        channel_first_waits: dict[str, list[int]] = defaultdict(list)

        for row in rows:
            manager_user_id = str(row["manager_user_id"])
            if manager_filter is not None and manager_user_id != manager_filter:
                continue

            response_at = _parse_timestamp(str(row["to_first_sent_at"]))
            if response_at < start_at or response_at > reference:
                continue

            wait_seconds = int(row["wait_seconds"])
            channel = str(row["channel"])

            response_waits.append(wait_seconds)
            manager_waits[manager_user_id].append(wait_seconds)
            channel_waits[channel].append(wait_seconds)

            if int(row["is_first_manager_response"]) == 1:
                first_response_waits.append(wait_seconds)
                manager_first_waits[manager_user_id].append(wait_seconds)
                channel_first_waits[channel].append(wait_seconds)

        manager_rows: list[OpenLinesManagerResponseRow] = []
        for user_id, waits in manager_waits.items():
            identity = directory.get(user_id)
            first_waits = manager_first_waits.get(user_id, [])
            manager_rows.append(
                OpenLinesManagerResponseRow(
                    manager_user_id=user_id,
                    manager_label=_manager_label(directory, user_id),
                    active=identity.active if identity is not None else False,
                    response_events=len(waits),
                    first_response_events=len(first_waits),
                    response_median_seconds=_median(waits),
                    response_p90_seconds=_p90(waits),
                    first_response_median_seconds=_median(first_waits),
                    first_response_p90_seconds=_p90(first_waits),
                )
            )

        manager_rows.sort(key=lambda item: (-item.response_events, int(item.manager_user_id)))

        channel_rows: list[OpenLinesChannelResponseRow] = []
        for channel, waits in channel_waits.items():
            first_waits = channel_first_waits.get(channel, [])
            channel_rows.append(
                OpenLinesChannelResponseRow(
                    channel=channel,
                    response_events=len(waits),
                    first_response_events=len(first_waits),
                    response_median_seconds=_median(waits),
                    response_p90_seconds=_p90(waits),
                )
            )

        channel_rows.sort(key=lambda item: (-item.response_events, item.channel))

        client_tail_candidates: int | None = None
        initial_no_response_candidates: int | None = None

        if manager_filter is None:
            cursor = await database.execute(
                """
                SELECT
                    thread.last_sent_at,
                    metric.client_tail_after_dialogue,
                    metric.initial_client_without_manager_response
                FROM conversation_thread_metrics AS metric
                JOIN conversation_threads AS thread
                  ON thread.chat_id = metric.chat_id
                WHERE metric.client_tail_after_dialogue = 1
                   OR metric.initial_client_without_manager_response = 1
                """
            )
            candidate_rows = await cursor.fetchall()

            client_tail_candidates = 0
            initial_no_response_candidates = 0

            for row in candidate_rows:
                last_activity_at = _parse_timestamp(str(row["last_sent_at"]))
                if last_activity_at < start_at or last_activity_at > reference:
                    continue
                client_tail_candidates += int(row["client_tail_after_dialogue"])
                initial_no_response_candidates += int(
                    row["initial_client_without_manager_response"]
                )

    return OpenLinesResponseReport(
        days=days,
        start_at=start_at,
        end_at=reference,
        manager_filter=manager_filter,
        manager_filter_label=manager_filter_label,
        response_events=len(response_waits),
        first_response_events=len(first_response_waits),
        response_median_seconds=_median(response_waits),
        response_p90_seconds=_p90(response_waits),
        first_response_median_seconds=_median(first_response_waits),
        first_response_p90_seconds=_p90(first_response_waits),
        current_client_tail_candidates=client_tail_candidates,
        current_initial_no_response_candidates=initial_no_response_candidates,
        managers=tuple(manager_rows),
        channels=tuple(channel_rows),
    )


def format_openlines_response_for_ai(
    report: OpenLinesResponseReport,
    *,
    manager_limit: int = 10,
) -> str:
    scope = (
        f"менеджер: {report.manager_filter_label}"
        if report.manager_filter_label is not None
        else "команда"
    )

    lines = [
        f"ИИ-РОП · Open Lines Response Facts · rolling {report.days}×24ч · {scope}",
        f"• Client→manager response events: {report.response_events}",
        f"• Из них first-manager-response events: {report.first_response_events}",
        "• Calendar wait client→manager · median: "
        f"{_format_duration(report.response_median_seconds)}",
        f"• Calendar wait client→manager · p90: {_format_duration(report.response_p90_seconds)}",
        "• First-manager-response events · median: "
        f"{_format_duration(report.first_response_median_seconds)}",
        "• First-manager-response events · p90: "
        f"{_format_duration(report.first_response_p90_seconds)}",
    ]

    if report.current_client_tail_candidates is not None:
        lines.extend(
            [
                "• Current client-tail candidates с последней human activity в окне: "
                f"{report.current_client_tail_candidates}",
                "• Initial client threads без наблюдаемого later manager response "
                "с последней human activity в окне: "
                f"{report.current_initial_no_response_candidates}",
            ]
        )

    if report.channels:
        lines.append("")
        lines.append("Каналы · factual response events:")
        for row in report.channels:
            lines.append(
                f"• {row.channel}: {row.response_events} ответов; "
                f"first {row.first_response_events}; "
                f"median {_format_duration(row.response_median_seconds)}; "
                f"p90 {_format_duration(row.response_p90_seconds)}"
            )

    if report.manager_filter is None and report.managers:
        lines.append("")
        lines.append(
            "Менеджеры · по объёму наблюдаемых client→manager response events "
            "(это НЕ рейтинг качества):"
        )
        for row in report.managers[:manager_limit]:
            status = "active" if row.active else "inactive/history"
            lines.append(
                f"• {row.manager_label} · {status}: "
                f"{row.response_events} ответов; first {row.first_response_events}; "
                f"median {_format_duration(row.response_median_seconds)}; "
                f"p90 {_format_duration(row.response_p90_seconds)}"
            )

    lines.extend(
        [
            "",
            "Методология / ограничения:",
            "• источник = реальные human turns Open Lines (client ↔ DIRECTORY_USER manager);",
            "• response event попадает в rolling-окно по timestamp начала manager turn;",
            "• ожидание = от конца client turn до начала следующего manager turn;",
            "• first-manager-response = первый manager turn после первого client turn в чате;",
            "• это calendar elapsed factual evidence, а не First Response SLA;",
            "• рабочие часы, выходные, праздники и business threshold не вычитаются;",
            "• client-tail = кандидат на незавершённый хвост, "
            "а не автоматическая ошибка менеджера;",
            "• team current-tail counts фильтруются по времени последней human activity;",
            "• при фильтре конкретного менеджера tail/no-response не приписываются ему без "
            "отдельного ownership/reassignment evidence;",
            "• старый CRM lead response evidence остаётся отдельным источником и этим отчётом "
            "не подменяется;",
            "• никаких SLA breach, pass/fail, good/bad score или причин результата здесь нет.",
        ]
    )

    return "\n".join(lines)
