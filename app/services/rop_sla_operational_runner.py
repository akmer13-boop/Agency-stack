from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.rop_realtime_sla_orchestrator import (
    process_realtime_sla_batch,
)
from app.services.rop_sla_deadline_sweep import (
    process_sla_deadline_batch,
)
from app.storage.rop_operational_coverage_store import (
    RopOperationalCoverageStore,
)


@dataclass(frozen=True, slots=True)
class OperationalSlaCycleResult:
    ready: bool
    events_processed: int
    event_failures: int
    evaluations_written: int
    deadlines_processed: int
    deadline_failures: int
    missing_sources: tuple[str, ...]
    lagging_sources: tuple[str, ...]


async def run_operational_sla_cycle(
    database_path: str,
    *,
    as_of: datetime | None = None,
    event_limit: int = 100,
    deadline_limit: int = 100,
    max_attempts: int = 3,
) -> OperationalSlaCycleResult:
    if event_limit < 1:
        raise ValueError("event_limit must be positive")

    if deadline_limit < 1:
        raise ValueError("deadline_limit must be positive")

    observed = as_of if as_of is not None else datetime.now(UTC)

    if observed.tzinfo is None:
        raise ValueError("as_of_timezone_missing")

    observed = observed.astimezone(UTC)

    coverage = RopOperationalCoverageStore(database_path)

    status = await coverage.operational_status(as_of=observed)

    if not status.ready:
        return OperationalSlaCycleResult(
            ready=False,
            events_processed=0,
            event_failures=0,
            evaluations_written=0,
            deadlines_processed=0,
            deadline_failures=0,
            missing_sources=(status.missing_sources),
            lagging_sources=(status.lagging_sources),
        )

    events = await process_realtime_sla_batch(
        database_path,
        limit=event_limit,
        as_of=observed,
        max_attempts=max_attempts,
    )

    deadlines = await process_sla_deadline_batch(
        database_path,
        limit=deadline_limit,
        as_of=observed,
        max_attempts=max_attempts,
    )

    return OperationalSlaCycleResult(
        ready=True,
        events_processed=len(events),
        event_failures=sum(item.outcome == "failed" for item in events),
        evaluations_written=sum(item.evaluations_written for item in events),
        deadlines_processed=len(deadlines),
        deadline_failures=sum(item.outcome == "failed" for item in deadlines),
        missing_sources=(),
        lagging_sources=(),
    )
