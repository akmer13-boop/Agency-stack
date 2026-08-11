from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id",
    default="-",
)


class JsonFormatter(logging.Formatter):
    """Render application logs as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }

        for key in (
            "event",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "agent",
            "attempt",
            "retry_in_seconds",
            "error_code",
            "state",
            "job_count",
            "recipient_count",
            "blockers",
            "due",
            "delivered",
            "failed",
            "job",
            "period_key",
            "recipient_id",
            "health_operation",
            "health_status",
            "consecutive_failures",
            "last_tick_age_seconds",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # HTTPX INFO messages include the full request URL. For Bitrix incoming
    # webhooks that URL contains the secret token, so never emit those logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_correlation_id() -> str:
    return correlation_id_var.get()
