from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.services.rop_b2c_first_response_truth import (
    B2CFirstResponseTruth,
    build_b2c_first_response_truth,
)
from app.services.rop_b2c_mvp_dashboard import (
    B2CMvpDashboard,
    B2CPeriodFlow,
    build_b2c_mvp_dashboard,
    build_b2c_period_flow,
)
from app.services.rop_b2c_stage_sla_truth import STAGE_LABELS
from app.services.rop_scheduler import RopSchedulerJobKind

_WEEK_KEY = re.compile(r"^(\d{4})-W(\d{2})$")


@dataclass(frozen=True, slots=True)
class RopCalendarWindow:
    kind: RopSchedulerJobKind
    period_key: str
    timezone_name: str
    local_start: date
    local_end: date
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class RopCalendarReport:
    window: RopCalendarWindow
    flow: B2CPeriodFlow
    first_response: B2CFirstResponseTruth
    current: B2CMvpDashboard


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def build_rop_calendar_window(
    kind: RopSchedulerJobKind,
    *,
    period_key: str,
    timezone_name: str,
) -> RopCalendarWindow:
    timezone = _timezone(timezone_name)

    if kind is RopSchedulerJobKind.DAILY:
        try:
            scheduled_date = date.fromisoformat(period_key)
        except ValueError as exc:
            raise ValueError("daily period_key must be YYYY-MM-DD") from exc
        local_start = scheduled_date - timedelta(days=1)
        local_end = local_start
    else:
        match = _WEEK_KEY.fullmatch(period_key)
        if match is None:
            raise ValueError("weekly period_key must be YYYY-Www")
        try:
            current_week_start = date.fromisocalendar(
                int(match.group(1)),
                int(match.group(2)),
                1,
            )
        except ValueError as exc:
            raise ValueError("weekly period_key is invalid") from exc
        local_start = current_week_start - timedelta(days=7)
        local_end = current_week_start - timedelta(days=1)

    local_start_at = datetime.combine(
        local_start,
        time.min,
        tzinfo=timezone,
    )
    local_end_exclusive = datetime.combine(
        local_end + timedelta(days=1),
        time.min,
        tzinfo=timezone,
    )

    return RopCalendarWindow(
        kind=kind,
        period_key=period_key,
        timezone_name=timezone_name,
        local_start=local_start,
        local_end=local_end,
        start_at=local_start_at.astimezone(UTC),
        end_at=(
            local_end_exclusive.astimezone(UTC)
            - timedelta(microseconds=1)
        ),
    )


def _period_label(window: RopCalendarWindow) -> str:
    if window.local_start == window.local_end:
        return window.local_start.strftime("%d.%m.%Y")
    if (
        window.local_start.month == window.local_end.month
        and window.local_start.year == window.local_end.year
    ):
        return (
            f"{window.local_start.day:02d}–"
            f"{window.local_end.strftime('%d.%m.%Y')}"
        )
    return (
        f"{window.local_start.strftime('%d.%m.%Y')}–"
        f"{window.local_end.strftime('%d.%m.%Y')}"
    )


def _title(kind: RopSchedulerJobKind) -> str:
    if kind is RopSchedulerJobKind.DAILY:
        return "ИИ-РОП · итоги дня"
    return "ИИ-РОП · итоги недели"


def format_rop_calendar_report(report: RopCalendarReport) -> str:
    window = report.window
    flow = report.flow
    first_response = report.first_response
    current = report.current
    stage = current.stage_sla

    policy_not_configured = dict(stage.blocked_reasons).get(
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

    if first_response.closed_measured:
        first_response_compliance = (
            f"{first_response.ok_share_closed_percent:.1f}% "
            f"(n={first_response.closed_measured})"
        )
    else:
        first_response_compliance = "не рассчитано (n=0)"

    lines = [
        _title(window.kind),
        f"Период: {_period_label(window)} · МСК",
        "",
        "Итоги периода",
        (
            f"• подтверждённые B2C-лиды: "
            f"{first_response.b2c_proven}"
        ),
        f"• новые B2C-сделки: {flow.new_deals}",
        (
            f"• WON / LOST: {flow.won} / {flow.lost} "
            f"· конверсия {flow.conversion_percent:.1f}% "
            f"(n={flow.closed})"
        ),
        "",
        "Первый ответ · 15 бизнес-минут",
        f"• соблюдение: {first_response_compliance}",
        (
            f"• нарушений: {first_response.breach} "
            f"· недостаточно данных: {first_response.blocked}"
        ),
    ]

    if first_response.open:
        lines.append(
            f"• таймер ещё открыт на конец периода: {first_response.open}"
        )

    cutoff_label = current.cutoff_at.astimezone(
        _timezone(window.timezone_name)
    ).strftime("%d.%m.%Y %H:%M")
    lines.extend(
        [
            "",
            "Текущий backlog",
            f"• активные B2C-сделки: {current.active_b2c_deals}",
            "",
            f"Stage SLA · подтверждённый срез на {cutoff_label} МСК",
            (
                f"• требуют внимания: {stage.attention} "
                f"· недостаточно данных: {evidence_blocked}"
            ),
            f"• SLA пока не настроен: {policy_not_configured}",
        ]
    )

    if top_attention_stage is not None:
        stage_id, _total, _open, attention, _blocked = (
            top_attention_stage
        )
        if attention > 0:
            lines.append(
                "• главная проблемная стадия: "
                f"{STAGE_LABELS.get(stage_id, stage_id)} "
                f"({attention})"
            )

    lines.extend(
        [
            "",
            (
                "Итоги относятся только к указанному календарному "
                "периоду; состояние CRM — текущий backlog."
            ),
            (
                "Недостаточно данных не считается нарушением. "
                "Bitrix24 не изменяется."
            ),
            "BITRIX WRITES = NONE",
        ]
    )
    return "\n".join(lines)


def build_rop_calendar_report(
    settings: Settings,
    *,
    kind: RopSchedulerJobKind,
    period_key: str,
) -> str:
    window = build_rop_calendar_window(
        kind,
        period_key=period_key,
        timezone_name=settings.rop_timezone,
    )
    flow = build_b2c_period_flow(
        settings,
        window_start=window.start_at,
        window_end=window.end_at,
    )
    first_response = build_b2c_first_response_truth(
        settings.database_path,
        now=window.end_at,
        window_start=window.start_at,
    )
    current = build_b2c_mvp_dashboard(settings)
    return format_rop_calendar_report(
        RopCalendarReport(
            window=window,
            flow=flow,
            first_response=first_response,
            current=current,
        )
    )
