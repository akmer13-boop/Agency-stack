from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.config import Settings
from app.integrations.bitrix24 import (
    Bitrix24ConfigurationError,
    Bitrix24RequestError,
)
from app.services.bitrix24_sync import (
    Bitrix24SyncStateError,
    BitrixSyncResult,
    build_sync_client,
    run_incremental_bitrix_sync,
)
from app.services.openlines_ingestion import run_openlines_ingestion
from app.services.rop_voximplant_reconciliation import (
    VoximplantReconciliationResult,
    reconcile_voximplant_statistics,
)
from app.storage.rop_voximplant_reconciliation_store import (
    RopVoximplantReconciliationStore,
)

logger = logging.getLogger(__name__)

bitrix_sync_lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class BitrixAutoSyncTickResult:
    outcome: str
    run_id: int | None = None
    counts: tuple[tuple[str, int], ...] = ()
    checkpoint: str | None = None
    error_code: str | None = None
    openlines_outcome: str = "disabled"
    openlines_crm_objects_processed: int = 0
    openlines_chats_discovered: int = 0
    openlines_chats_processed: int = 0
    openlines_messages_observed: int = 0
    openlines_error_count: int = 0
    openlines_error_code: str | None = None
    voximplant_outcome: str = "disabled"
    voximplant_run_id: int | None = None
    voximplant_window_start: str | None = None
    voximplant_window_end: str | None = None
    voximplant_fetched_rows: int = 0
    voximplant_policy_candidate_calls: int = 0
    voximplant_error_code: str | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class BitrixAutoSyncHealthStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}

        try:
            value = json.loads(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {
                "state": "unreadable",
                "last_error_code": "health_file_unreadable",
            }

        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(
                value,
                temporary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            temporary.write("\n")
            temporary_path = Path(temporary.name)

        temporary_path.replace(self.path)

    def record_startup(
        self,
        settings: Settings,
    ) -> None:
        previous = self._read()
        self._write(
            {
                **previous,
                "version": 3,
                "state": (
                    "ready"
                    if settings.bitrix24_auto_sync_enabled
                    else "disabled"
                ),
                "enabled": settings.bitrix24_auto_sync_enabled,
                "poll_seconds": settings.bitrix24_auto_sync_poll_seconds,
                "openlines_enabled": (
                    settings.bitrix24_auto_sync_openlines_enabled
                ),
                "voximplant_enabled": (
                    settings.bitrix24_auto_sync_voximplant_enabled
                ),
                "started_at": _utc_now(),
            }
        )

    def record_attempt(self) -> None:
        previous = self._read()
        self._write(
            {
                **previous,
                "state": "running",
                "last_attempt_at": _utc_now(),
            }
        )

    def record_success(
        self,
        result: BitrixSyncResult,
        *,
        openlines_outcome: str = "disabled",
        openlines_crm_objects_processed: int = 0,
        openlines_chats_discovered: int = 0,
        openlines_chats_processed: int = 0,
        openlines_messages_observed: int = 0,
        openlines_error_count: int = 0,
        openlines_error_code: str | None = None,
        voximplant_outcome: str = "disabled",
        voximplant_run_id: int | None = None,
        voximplant_window_start: str | None = None,
        voximplant_window_end: str | None = None,
        voximplant_fetched_rows: int = 0,
        voximplant_policy_candidate_calls: int = 0,
        voximplant_error_code: str | None = None,
    ) -> None:
        previous = self._read()
        now = _utc_now()
        openlines_success_at = previous.get(
            "last_openlines_success_at"
        )
        if openlines_outcome in {
            "completed",
            "completed_with_errors",
        }:
            openlines_success_at = now
        voximplant_success_at = previous.get(
            "last_voximplant_success_at"
        )
        if voximplant_outcome == "completed":
            voximplant_success_at = now
        self._write(
            {
                **previous,
                "state": "ready",
                "last_success_at": now,
                "last_run_id": result.run_id,
                "last_checkpoint": result.checkpoint,
                "last_counts": result.counts,
                "last_error_code": None,
                "consecutive_failures": 0,
                "last_openlines_outcome": openlines_outcome,
                "last_openlines_success_at": openlines_success_at,
                "last_openlines_crm_objects_processed": (
                    openlines_crm_objects_processed
                ),
                "last_openlines_chats_discovered": (
                    openlines_chats_discovered
                ),
                "last_openlines_chats_processed": (
                    openlines_chats_processed
                ),
                "last_openlines_messages_observed": (
                    openlines_messages_observed
                ),
                "last_openlines_error_count": openlines_error_count,
                "last_openlines_error_code": openlines_error_code,
                "last_voximplant_outcome": voximplant_outcome,
                "last_voximplant_success_at": voximplant_success_at,
                "last_voximplant_run_id": voximplant_run_id,
                "last_voximplant_window_start": voximplant_window_start,
                "last_voximplant_window_end": voximplant_window_end,
                "last_voximplant_fetched_rows": voximplant_fetched_rows,
                "last_voximplant_policy_candidate_calls": (
                    voximplant_policy_candidate_calls
                ),
                "last_voximplant_error_code": voximplant_error_code,
            }
        )

    def record_skipped_busy(self) -> None:
        previous = self._read()
        self._write(
            {
                **previous,
                "state": "ready",
                "last_skipped_at": _utc_now(),
                "last_skip_reason": "sync_busy",
            }
        )

    def record_failure(self, error_code: str) -> None:
        previous = self._read()
        failures = int(
            previous.get("consecutive_failures") or 0
        ) + 1
        self._write(
            {
                **previous,
                "state": "error",
                "last_failure_at": _utc_now(),
                "last_error_code": error_code[:120],
                "consecutive_failures": failures,
            }
        )

    def read(self) -> dict[str, Any]:
        return self._read()


async def _run_voximplant_auto_sync(
    settings: Settings,
    *,
    now: datetime,
) -> tuple[
    VoximplantReconciliationResult,
    datetime,
    datetime,
]:
    observed_at = now.astimezone(UTC)
    window_end = observed_at - timedelta(
        minutes=(
            settings.bitrix24_auto_sync_voximplant_settle_minutes
        )
    )

    store = RopVoximplantReconciliationStore(
        settings.database_path
    )
    coverage = await store.get_coverage()

    if coverage is None:
        window_start = window_end - timedelta(
            days=(
                settings.bitrix24_auto_sync_voximplant_initial_lookback_days
            )
        )
    else:
        anchor = min(
            coverage.window_end,
            window_end,
        )
        window_start = anchor - timedelta(
            minutes=(
                settings.bitrix24_auto_sync_voximplant_overlap_minutes
            )
        )

    if window_start >= window_end:
        window_start = window_end - timedelta(minutes=1)

    result = await reconcile_voximplant_statistics(
        settings.database_path,
        build_sync_client(settings),
        window_start=window_start,
        window_end=window_end,
        max_pages=(
            settings.bitrix24_auto_sync_voximplant_max_pages
        ),
    )

    return result, window_start, window_end


async def run_bitrix_auto_sync_tick(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> BitrixAutoSyncTickResult:
    if not settings.bitrix24_auto_sync_enabled:
        return BitrixAutoSyncTickResult(
            outcome="disabled"
        )

    if bitrix_sync_lock.locked():
        return BitrixAutoSyncTickResult(
            outcome="skipped_busy"
        )

    reference = (now or datetime.now(UTC)).astimezone(UTC)

    async with bitrix_sync_lock:
        try:
            core_result = await run_incremental_bitrix_sync(
                settings
            )
        except Bitrix24SyncStateError:
            return BitrixAutoSyncTickResult(
                outcome="failed",
                error_code="completed_full_sync_required",
            )
        except Bitrix24ConfigurationError:
            return BitrixAutoSyncTickResult(
                outcome="failed",
                error_code="bitrix24_not_configured",
            )
        except Bitrix24RequestError as exc:
            return BitrixAutoSyncTickResult(
                outcome="failed",
                error_code=(
                    exc.error_code
                    or "bitrix24_request_error"
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected Bitrix auto-sync failure",
                extra={
                    "event": "bitrix_auto_sync_unexpected_failure",
                    "error_type": type(exc).__name__,
                },
            )
            return BitrixAutoSyncTickResult(
                outcome="failed",
                error_code=type(exc).__name__,
            )

        openlines_outcome = "disabled"
        openlines_crm_objects_processed = 0
        openlines_chats_discovered = 0
        openlines_chats_processed = 0
        openlines_messages_observed = 0
        openlines_error_count = 0
        openlines_error_code = None

        if settings.bitrix24_auto_sync_openlines_enabled:
            if not core_result.checkpoint:
                openlines_outcome = "skipped_no_checkpoint"
            else:
                try:
                    openlines_result = await run_openlines_ingestion(
                        settings,
                        max_crm_objects=(
                            settings.bitrix24_auto_sync_openlines_max_crm_objects
                        ),
                        max_chats=(
                            settings.bitrix24_auto_sync_openlines_max_chats
                        ),
                        max_pages_per_chat=(
                            settings.bitrix24_auto_sync_openlines_max_pages_per_chat
                        ),
                        run_discovery=True,
                        run_backfill=True,
                        recent_modified_since=core_result.checkpoint,
                    )
                    openlines_crm_objects_processed = (
                        openlines_result.crm_objects_processed
                    )
                    openlines_chats_discovered = (
                        openlines_result.chats_discovered
                    )
                    openlines_chats_processed = (
                        openlines_result.chats_processed
                    )
                    openlines_messages_observed = (
                        openlines_result.messages_observed
                    )
                    openlines_error_count = sum(
                        count
                        for _, count in openlines_result.errors
                    )
                    if openlines_error_count:
                        openlines_outcome = "completed_with_errors"
                        openlines_error_code = ", ".join(
                            f"{code}:{count}"
                            for code, count in openlines_result.errors
                        )[:120]
                    else:
                        openlines_outcome = "completed"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    openlines_outcome = "failed"
                    openlines_error_count = 1
                    openlines_error_code = type(exc).__name__
                    logger.exception(
                        "OpenLines auto-sync failed after successful CRM sync",
                        extra={
                            "event": "openlines_auto_sync_failure",
                            "run_id": core_result.run_id,
                            "error_type": type(exc).__name__,
                        },
                    )

        voximplant_outcome = "disabled"
        voximplant_run_id = None
        voximplant_window_start = None
        voximplant_window_end = None
        voximplant_fetched_rows = 0
        voximplant_policy_candidate_calls = 0
        voximplant_error_code = None

        if settings.bitrix24_auto_sync_voximplant_enabled:
            try:
                (
                    voximplant_result,
                    vox_start,
                    vox_end,
                ) = await _run_voximplant_auto_sync(
                    settings,
                    now=reference,
                )
                voximplant_outcome = "completed"
                voximplant_run_id = voximplant_result.run_id
                voximplant_window_start = vox_start.isoformat()
                voximplant_window_end = vox_end.isoformat()
                voximplant_fetched_rows = (
                    voximplant_result.fetched_rows
                )
                voximplant_policy_candidate_calls = (
                    voximplant_result.policy_candidate_calls
                )
            except asyncio.CancelledError:
                raise
            except Bitrix24RequestError as exc:
                voximplant_outcome = "failed"
                voximplant_error_code = (
                    exc.error_code
                    or "bitrix24_request_error"
                )
                logger.warning(
                    "Voximplant API rejected auto-sync after successful CRM sync",
                    extra={
                        "event": "voximplant_auto_sync_request_failure",
                        "run_id": core_result.run_id,
                        "error_code": voximplant_error_code,
                    },
                )
            except Exception as exc:
                voximplant_outcome = "failed"
                voximplant_error_code = type(exc).__name__
                logger.exception(
                    "Voximplant auto-sync failed after successful CRM sync",
                    extra={
                        "event": "voximplant_auto_sync_failure",
                        "run_id": core_result.run_id,
                        "error_type": type(exc).__name__,
                    },
                )

    return BitrixAutoSyncTickResult(
        outcome="completed",
        run_id=core_result.run_id,
        counts=tuple(sorted(core_result.counts.items())),
        checkpoint=core_result.checkpoint,
        openlines_outcome=openlines_outcome,
        openlines_crm_objects_processed=(
            openlines_crm_objects_processed
        ),
        openlines_chats_discovered=openlines_chats_discovered,
        openlines_chats_processed=openlines_chats_processed,
        openlines_messages_observed=openlines_messages_observed,
        openlines_error_count=openlines_error_count,
        openlines_error_code=openlines_error_code,
        voximplant_outcome=voximplant_outcome,
        voximplant_run_id=voximplant_run_id,
        voximplant_window_start=voximplant_window_start,
        voximplant_window_end=voximplant_window_end,
        voximplant_fetched_rows=voximplant_fetched_rows,
        voximplant_policy_candidate_calls=(
            voximplant_policy_candidate_calls
        ),
        voximplant_error_code=voximplant_error_code,
    )


async def run_bitrix_auto_sync_worker(
    settings: Settings,
) -> None:
    health = BitrixAutoSyncHealthStore(
        settings.bitrix24_auto_sync_health_path
    )
    health.record_startup(settings)

    logger.info(
        "Bitrix auto-sync startup",
        extra={
            "event": "bitrix_auto_sync_startup",
            "state": (
                "ready"
                if settings.bitrix24_auto_sync_enabled
                else "disabled"
            ),
            "poll_seconds": settings.bitrix24_auto_sync_poll_seconds,
        },
    )

    if not settings.bitrix24_auto_sync_enabled:
        return

    while True:
        health.record_attempt()
        result = await run_bitrix_auto_sync_tick(
            settings
        )

        if result.outcome == "completed":
            health.record_success(
                BitrixSyncResult(
                    run_id=result.run_id or 0,
                    counts=dict(result.counts),
                    mode="incremental",
                    checkpoint=result.checkpoint,
                ),
                openlines_outcome=result.openlines_outcome,
                openlines_crm_objects_processed=(
                    result.openlines_crm_objects_processed
                ),
                openlines_chats_discovered=(
                    result.openlines_chats_discovered
                ),
                openlines_chats_processed=(
                    result.openlines_chats_processed
                ),
                openlines_messages_observed=(
                    result.openlines_messages_observed
                ),
                openlines_error_count=(
                    result.openlines_error_count
                ),
                openlines_error_code=(
                    result.openlines_error_code
                ),
                voximplant_outcome=(
                    result.voximplant_outcome
                ),
                voximplant_run_id=(
                    result.voximplant_run_id
                ),
                voximplant_window_start=(
                    result.voximplant_window_start
                ),
                voximplant_window_end=(
                    result.voximplant_window_end
                ),
                voximplant_fetched_rows=(
                    result.voximplant_fetched_rows
                ),
                voximplant_policy_candidate_calls=(
                    result.voximplant_policy_candidate_calls
                ),
                voximplant_error_code=(
                    result.voximplant_error_code
                ),
            )
            logger.info(
                "Bitrix auto-sync completed",
                extra={
                    "event": "bitrix_auto_sync_completed",
                    "run_id": result.run_id,
                    "counts": dict(result.counts),
                    "openlines_outcome": result.openlines_outcome,
                    "openlines_chats_processed": (
                        result.openlines_chats_processed
                    ),
                    "openlines_error_count": (
                        result.openlines_error_count
                    ),
                    "voximplant_outcome": (
                        result.voximplant_outcome
                    ),
                    "voximplant_run_id": (
                        result.voximplant_run_id
                    ),
                    "voximplant_fetched_rows": (
                        result.voximplant_fetched_rows
                    ),
                },
            )
        elif result.outcome == "skipped_busy":
            health.record_skipped_busy()
            logger.info(
                "Bitrix auto-sync skipped because another sync is running",
                extra={
                    "event": "bitrix_auto_sync_skipped_busy",
                },
            )
        else:
            error_code = (
                result.error_code
                or "bitrix_auto_sync_failed"
            )
            health.record_failure(error_code)
            logger.warning(
                "Bitrix auto-sync failed",
                extra={
                    "event": "bitrix_auto_sync_failed",
                    "error_code": error_code,
                },
            )

        await asyncio.sleep(
            settings.bitrix24_auto_sync_poll_seconds
        )


def format_bitrix_auto_sync_status(
    settings: Settings,
) -> str:
    health = BitrixAutoSyncHealthStore(
        settings.bitrix24_auto_sync_health_path
    ).read()

    enabled = settings.bitrix24_auto_sync_enabled
    lines = [
        "Bitrix24 → SQLite · Auto-sync",
        f"• включён: {'да' if enabled else 'нет'}",
        (
            "• интервал: "
            f"{settings.bitrix24_auto_sync_poll_seconds} сек."
        ),
        f"• состояние: {health.get('state') or 'ещё не запускался'}",
        f"• последний запуск: {health.get('last_attempt_at') or '—'}",
        f"• последний успех: {health.get('last_success_at') or '—'}",
        f"• последний Run ID: {health.get('last_run_id') or '—'}",
        (
            "• последовательных ошибок: "
            f"{health.get('consecutive_failures') or 0}"
        ),
    ]

    error_code = health.get("last_error_code")
    if error_code:
        lines.append(
            f"• последняя ошибка: {error_code}"
        )

    counts = health.get("last_counts")
    if isinstance(counts, dict) and counts:
        lines.append("• получено в последнем окне:")
        for entity_type, count in sorted(counts.items()):
            lines.append(
                f"  - {entity_type}: {count}"
            )

    openlines_enabled = (
        settings.bitrix24_auto_sync_openlines_enabled
    )
    lines.extend(
        [
            "",
            "OpenLines · свежие чаты",
            (
                "• включён: "
                f"{'да' if openlines_enabled else 'нет'}"
            ),
        ]
    )
    if openlines_enabled:
        lines.extend(
            [
                (
                    "• состояние: "
                    f"{health.get('last_openlines_outcome') or 'ещё не запускался'}"
                ),
                (
                    "• последний успех: "
                    f"{health.get('last_openlines_success_at') or '—'}"
                ),
                (
                    "• CRM-объектов проверено: "
                    f"{health.get('last_openlines_crm_objects_processed') or 0}"
                ),
                (
                    "• связей чатов найдено: "
                    f"{health.get('last_openlines_chats_discovered') or 0}"
                ),
                (
                    "• чатов обновлено: "
                    f"{health.get('last_openlines_chats_processed') or 0}"
                ),
                (
                    "• сообщений просмотрено: "
                    f"{health.get('last_openlines_messages_observed') or 0}"
                ),
                (
                    "• ошибок OpenLines: "
                    f"{health.get('last_openlines_error_count') or 0}"
                ),
            ]
        )
        openlines_error = health.get(
            "last_openlines_error_code"
        )
        if openlines_error:
            lines.append(
                f"• последняя ошибка OpenLines: {openlines_error}"
            )

    voximplant_enabled = (
        settings.bitrix24_auto_sync_voximplant_enabled
    )
    lines.extend(
        [
            "",
            "Voximplant · свежие звонки",
            (
                "• включён: "
                f"{'да' if voximplant_enabled else 'нет'}"
            ),
        ]
    )
    if voximplant_enabled:
        lines.extend(
            [
                (
                    "• состояние: "
                    f"{health.get('last_voximplant_outcome') or 'ещё не запускался'}"
                ),
                (
                    "• последний успех: "
                    f"{health.get('last_voximplant_success_at') or '—'}"
                ),
                (
                    "• последний Run ID: "
                    f"{health.get('last_voximplant_run_id') or '—'}"
                ),
                (
                    "• окно последнего запроса: "
                    f"{health.get('last_voximplant_window_start') or '—'} → "
                    f"{health.get('last_voximplant_window_end') or '—'}"
                ),
                (
                    "• звонков получено: "
                    f"{health.get('last_voximplant_fetched_rows') or 0}"
                ),
                (
                    "• кандидатов для SLA: "
                    f"{health.get('last_voximplant_policy_candidate_calls') or 0}"
                ),
            ]
        )
        voximplant_error = health.get(
            "last_voximplant_error_code"
        )
        if voximplant_error:
            lines.append(
                f"• последняя ошибка Voximplant: {voximplant_error}"
            )

    lines.extend(
        [
            "",
            "Режим: read-only API Bitrix24 → локальный SQLite.",
            "BITRIX WRITES = NONE",
        ]
    )
    return "\n".join(lines)
