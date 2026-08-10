from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import Settings

SUPPORTED_TIMER_STARTS = frozenset({"lead_created"})
SUPPORTED_RESPONSE_EVENTS = frozenset({"manager_evidence", "confirmed_communication"})
SUPPORTED_CLOCKS = frozenset({"calendar_elapsed"})
SUPPORTED_REASSIGNMENT_MODES = frozenset({"not_attributed"})


class FirstResponsePolicyState(StrEnum):
    DISABLED = "disabled"
    BLOCKED = "blocked"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class FirstResponsePolicy:
    state: FirstResponsePolicyState
    enabled: bool
    timer_start: str
    response_event: str
    clock: str
    reassignment_mode: str
    threshold_seconds: int
    blockers: tuple[str, ...]


def _clean(value: str) -> str:
    return value.strip().lower()


def build_first_response_policy(settings: Settings) -> FirstResponsePolicy:
    timer_start = _clean(settings.rop_first_response_timer_start)
    response_event = _clean(settings.rop_first_response_event)
    clock = _clean(settings.rop_first_response_clock)
    reassignment_mode = _clean(settings.rop_first_response_reassignment_mode)
    threshold_seconds = settings.rop_first_response_threshold_seconds

    if not settings.rop_first_response_policy_enabled:
        return FirstResponsePolicy(
            state=FirstResponsePolicyState.DISABLED,
            enabled=False,
            timer_start=timer_start,
            response_event=response_event,
            clock=clock,
            reassignment_mode=reassignment_mode,
            threshold_seconds=threshold_seconds,
            blockers=("policy_disabled",),
        )

    blockers: list[str] = []

    if not timer_start:
        blockers.append("timer_start_missing")
    elif timer_start not in SUPPORTED_TIMER_STARTS:
        blockers.append(f"timer_start_unsupported:{timer_start}")

    if not response_event:
        blockers.append("response_event_missing")
    elif response_event not in SUPPORTED_RESPONSE_EVENTS:
        blockers.append(f"response_event_unsupported:{response_event}")

    if not clock:
        blockers.append("clock_missing")
    elif clock not in SUPPORTED_CLOCKS:
        blockers.append(f"clock_unsupported:{clock}")

    if not reassignment_mode:
        blockers.append("reassignment_mode_missing")
    elif reassignment_mode not in SUPPORTED_REASSIGNMENT_MODES:
        blockers.append(f"reassignment_mode_unsupported:{reassignment_mode}")

    if threshold_seconds <= 0:
        blockers.append("threshold_seconds_missing_or_invalid")

    state = FirstResponsePolicyState.BLOCKED if blockers else FirstResponsePolicyState.READY

    return FirstResponsePolicy(
        state=state,
        enabled=True,
        timer_start=timer_start,
        response_event=response_event,
        clock=clock,
        reassignment_mode=reassignment_mode,
        threshold_seconds=threshold_seconds,
        blockers=tuple(blockers),
    )


def format_first_response_policy_for_ai(policy: FirstResponsePolicy) -> str:
    threshold = str(policy.threshold_seconds) if policy.threshold_seconds > 0 else "NOT SET"
    lines = [
        "ИИ-РОП · First Response Policy Readiness",
        f"• state: {policy.state.value}",
        f"• enabled: {'yes' if policy.enabled else 'no'}",
        f"• timer_start: {policy.timer_start or 'NOT SET'}",
        f"• response_event: {policy.response_event or 'NOT SET'}",
        f"• clock: {policy.clock or 'NOT SET'}",
        f"• reassignment_mode: {policy.reassignment_mode or 'NOT SET'}",
        f"• threshold_seconds: {threshold}",
    ]

    if policy.blockers:
        lines.append("• blockers: " + ", ".join(policy.blockers))
    else:
        lines.append("• blockers: none")

    lines.extend(
        [
            "",
            "Ограничения:",
            "• этот tool проверяет только готовность конфигурации бизнес-правила;",
            "• он НЕ рассчитывает SLA compliance, pass/fail или просрочку по лидам;",
            "• значения не подставляются автоматически из observed baseline Stage 4.3;",
            "• business-hours clock и historical reassignment пока не реализованы;",
            "• READY означает только: все выбранные параметры поддерживаются текущим кодом.",
        ]
    )

    return "\n".join(lines)
