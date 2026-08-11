from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from aiogram import Bot

from app.config import Settings
from app.services.rop_analytics import build_rop_snapshot, format_rop_week
from app.services.rop_daily import build_rop_daily
from app.services.rop_scheduler import (
    RopScheduledDelivery,
    RopSchedulerJobKind,
    RopSchedulerLedger,
    RopSchedulerState,
    build_rop_scheduler_plan,
    due_rop_scheduler_deliveries,
)
from app.services.rop_scheduler_health import RopSchedulerHealthStore
from app.telegram.messages import split_telegram_text

logger = logging.getLogger(__name__)


def _record_scheduler_health(
    operation: str,
    callback: Callable[[], None],
) -> None:
    try:
        callback()
    except Exception:
        logger.exception(
            "ROP scheduler health state write failed",
            extra={
                "event": "rop_scheduler_health_write_failed",
                "health_operation": operation,
            },
        )


@dataclass(frozen=True, slots=True)
class RopSchedulerTickResult:
    state: RopSchedulerState
    due: int
    delivered: int
    failed: int


async def _build_weekly_report(settings: Settings) -> str:
    snapshot = await build_rop_snapshot(
        settings.database_path,
        attention_days=settings.rop_attention_days,
        critical_days=settings.rop_critical_days,
        risk_limit=settings.rop_risk_limit,
        timezone_name=settings.rop_timezone,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )
    return format_rop_week(snapshot)


async def _build_report(
    delivery: RopScheduledDelivery,
    settings: Settings,
) -> str:
    if delivery.job.kind is RopSchedulerJobKind.DAILY:
        return await build_rop_daily(settings)
    return await _build_weekly_report(settings)


async def _send_report(
    bot: Bot,
    recipient_id: int,
    text: str,
    settings: Settings,
) -> None:
    for chunk in split_telegram_text(text, settings.telegram_reply_chunk_size):
        await bot.send_message(chat_id=recipient_id, text=chunk)


async def run_rop_scheduler_tick(
    bot: Bot,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> RopSchedulerTickResult:
    plan = build_rop_scheduler_plan(settings)

    if plan.state is not RopSchedulerState.READY:
        return RopSchedulerTickResult(
            state=plan.state,
            due=0,
            delivered=0,
            failed=0,
        )

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    ledger = RopSchedulerLedger(settings.rop_scheduler_state_path)
    due = due_rop_scheduler_deliveries(plan, ledger, now=reference)

    if not due:
        return RopSchedulerTickResult(
            state=plan.state,
            due=0,
            delivered=0,
            failed=0,
        )

    by_job: defaultdict[str, list[RopScheduledDelivery]] = defaultdict(list)
    for delivery in due:
        by_job[delivery.job.name].append(delivery)

    delivered_count = 0
    failed_count = 0

    for deliveries in by_job.values():
        report = await _build_report(deliveries[0], settings)

        for delivery in deliveries:
            try:
                await _send_report(
                    bot,
                    delivery.recipient_id,
                    report,
                    settings,
                )
            except Exception:
                failed_count += 1
                logger.exception(
                    "Scheduled ROP report delivery failed",
                    extra={
                        "event": "rop_scheduler_delivery_failed",
                        "job": delivery.job.name,
                        "period_key": delivery.period_key,
                        "recipient_id": delivery.recipient_id,
                    },
                )
                continue

            ledger.mark_delivered(
                delivery.ledger_key,
                delivered_at=reference,
            )
            delivered_count += 1
            logger.info(
                "Scheduled ROP report delivered",
                extra={
                    "event": "rop_scheduler_delivery",
                    "job": delivery.job.name,
                    "period_key": delivery.period_key,
                    "recipient_id": delivery.recipient_id,
                },
            )

    return RopSchedulerTickResult(
        state=plan.state,
        due=len(due),
        delivered=delivered_count,
        failed=failed_count,
    )


async def run_rop_scheduler(
    bot: Bot,
    settings: Settings,
) -> None:
    plan = build_rop_scheduler_plan(settings)
    health_store = RopSchedulerHealthStore(settings.rop_scheduler_health_path)
    startup_at = datetime.now(UTC)
    _record_scheduler_health(
        "startup",
        lambda: health_store.record_startup(plan.state, at=startup_at),
    )

    logger.info(
        "ROP scheduler startup",
        extra={
            "event": "rop_scheduler_startup",
            "state": plan.state.value,
            "job_count": len(plan.jobs),
            "recipient_count": len(plan.recipients),
            "blockers": list(plan.blockers),
        },
    )

    if plan.state is not RopSchedulerState.READY:
        return

    while True:
        tick_started_at = datetime.now(UTC)
        _record_scheduler_health(
            "tick_started",
            partial(
                health_store.record_tick_started,
                plan.state,
                at=tick_started_at,
            ),
        )
        try:
            result = await run_rop_scheduler_tick(bot, settings)
            tick_completed_at = datetime.now(UTC)
            _record_scheduler_health(
                "tick_completed",
                partial(
                    health_store.record_tick_completed,
                    plan.state,
                    due=result.due,
                    delivered=result.delivered,
                    failed=result.failed,
                    at=tick_completed_at,
                ),
            )
            logger.info(
                "ROP scheduler tick completed",
                extra={
                    "event": "rop_scheduler_tick",
                    "due": result.due,
                    "delivered": result.delivered,
                    "failed": result.failed,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            tick_failed_at = datetime.now(UTC)
            _record_scheduler_health(
                "tick_error",
                partial(
                    health_store.record_tick_error,
                    plan.state,
                    error_code="tick_failed",
                    at=tick_failed_at,
                ),
            )
            logger.exception(
                "ROP scheduler tick failed",
                extra={"event": "rop_scheduler_tick_failed"},
            )

        await asyncio.sleep(settings.rop_scheduler_poll_seconds)
