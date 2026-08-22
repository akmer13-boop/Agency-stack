from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import Settings
from app.services.bitrix_event_processor import (
    process_bitrix_event_batch,
)
from app.services.rop_sla_operational_runner import (
    run_operational_sla_cycle,
)
from app.storage.bitrix_event_store import (
    BitrixEventInboxStore,
)
from app.storage.rop_lead_policy_profile_store import (
    RopLeadPolicyProfileStore,
)
from app.storage.rop_operational_coverage_store import (
    RopOperationalCoverageStore,
)
from app.storage.rop_sla_deadline_sweep_store import (
    RopSlaDeadlineSweepStore,
)
from app.storage.rop_sla_runtime_store import (
    RopSlaRuntimeStore,
)
from app.storage.rop_source_coverage_store import (
    RopSourceCoverageStore,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RopSlaBackgroundTickResult:
    events_processed: int
    event_failures: int
    operational_ready: bool
    evaluations_written: int
    deadlines_processed: int
    deadline_failures: int
    missing_sources: tuple[str, ...]
    lagging_sources: tuple[str, ...]


async def initialize_rop_sla_background_runtime(
    settings: Settings,
) -> None:
    database_path = settings.database_path

    await BitrixEventInboxStore(
        database_path
    ).initialize()

    await RopSlaRuntimeStore(
        database_path
    ).initialize()

    await RopSlaDeadlineSweepStore(
        database_path
    ).initialize()

    await RopSourceCoverageStore(
        database_path
    ).initialize()

    await RopOperationalCoverageStore(
        database_path
    ).initialize()

    profiles = RopLeadPolicyProfileStore(
        database_path
    )

    await asyncio.to_thread(
        profiles.initialize
    )


async def run_rop_sla_background_tick(
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> RopSlaBackgroundTickResult:
    observed = (
        as_of
        if as_of is not None
        else datetime.now(UTC)
    )

    if observed.tzinfo is None:
        raise ValueError(
            "as_of_timezone_missing"
        )

    observed = observed.astimezone(UTC)

    events = await process_bitrix_event_batch(
        settings,
        limit=settings.rop_sla_worker_event_limit,
        max_attempts=settings.rop_sla_worker_max_attempts,
    )

    operational = await run_operational_sla_cycle(
        settings.database_path,
        as_of=observed,
        event_limit=settings.rop_sla_worker_event_limit,
        deadline_limit=settings.rop_sla_worker_deadline_limit,
        max_attempts=settings.rop_sla_worker_max_attempts,
    )

    return RopSlaBackgroundTickResult(
        events_processed=len(events),
        event_failures=sum(
            item.outcome == "failed"
            for item in events
        ),
        operational_ready=operational.ready,
        evaluations_written=operational.evaluations_written,
        deadlines_processed=operational.deadlines_processed,
        deadline_failures=operational.deadline_failures,
        missing_sources=operational.missing_sources,
        lagging_sources=operational.lagging_sources,
    )


async def run_rop_sla_background_worker(
    settings: Settings,
) -> None:
    if not settings.rop_sla_worker_enabled:
        logger.info(
            "ROP SLA background worker disabled",
            extra={
                "event": "rop_sla_background_worker_disabled",
            },
        )
        return

    await initialize_rop_sla_background_runtime(
        settings
    )

    logger.info(
        "ROP SLA background worker started",
        extra={
            "event": "rop_sla_background_worker_started",
            "poll_seconds": settings.rop_sla_worker_poll_seconds,
        },
    )

    while True:
        try:
            result = await run_rop_sla_background_tick(
                settings
            )

            logger.info(
                "ROP SLA background tick completed",
                extra={
                    "event": "rop_sla_background_tick",
                    "events_processed": result.events_processed,
                    "event_failures": result.event_failures,
                    "operational_ready": result.operational_ready,
                    "evaluations_written": result.evaluations_written,
                    "deadlines_processed": result.deadlines_processed,
                    "deadline_failures": result.deadline_failures,
                    "missing_sources": list(
                        result.missing_sources
                    ),
                    "lagging_sources": list(
                        result.lagging_sources
                    ),
                },
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "ROP SLA background tick failed",
                extra={
                    "event": "rop_sla_background_tick_failed",
                },
            )

        await asyncio.sleep(
            settings.rop_sla_worker_poll_seconds
        )
