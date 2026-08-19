import logging
import secrets
import time
import uuid
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.observability import configure_logging, correlation_id_var, get_correlation_id
from app.runtime import configure_openai_runtime
from app.services.agent_runner import AgentExecutionError, execute_agent
from app.services.bitrix_realtime_events import (
    BitrixRealtimeEventError,
    ingest_bitrix_event,
)
from app.services.rop_scheduler_health import build_rop_scheduler_health

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
SettingsDependency = Annotated[Settings, Depends(get_settings)]
AuthorizationHeader = Annotated[str | None, Header()]

configure_openai_runtime(settings)

app = FastAPI(title=settings.app_name, version=settings.app_version)


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class AgentResponse(BaseModel):
    run_id: str
    status: Literal["completed"]
    answer: str
    agent: str


@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    token = correlation_id_var.set(correlation_id)
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        logger.info(
            "HTTP request completed",
            extra={
                "event": "http_request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
        )
        return response
    finally:
        correlation_id_var.reset(token)


def authorize(
    settings: SettingsDependency,
    authorization: AuthorizationHeader = None,
) -> None:
    if not settings.agent_api_token:
        raise HTTPException(status_code=503, detail="AGENT_API_TOKEN is not configured")

    expected = f"Bearer {settings.agent_api_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health(
    settings: SettingsDependency,
) -> dict[str, str | bool | int | None]:
    scheduler_health = build_rop_scheduler_health(settings)
    return {
        "status": "ok",
        "environment": settings.environment,
        "version": settings.app_version,
        "openai_configured": bool(settings.openai_api_key),
        "telegram_configured": bool(settings.telegram_bot_token),
        "bitrix24_configured": settings.bitrix24_configured,
        "bitrix_realtime_events_enabled": settings.bitrix_event_endpoint_enabled,
        "crm_write_enabled": settings.allow_crm_write,
        "rop_scheduler_state": scheduler_health.scheduler_state.value,
        "rop_scheduler_health": scheduler_health.status.value,
        "rop_scheduler_last_tick_at": (
            scheduler_health.last_tick_completed_at.isoformat()
            if scheduler_health.last_tick_completed_at is not None
            else None
        ),
        "rop_scheduler_consecutive_failures": scheduler_health.consecutive_failures,
    }


@app.post(
    "/api/v1/bitrix/events",
    status_code=202,
)
async def receive_bitrix_event(
    request: Request,
    settings: SettingsDependency,
) -> dict[
    str,
    str | int | bool | None,
]:
    if not (settings.bitrix_event_endpoint_enabled):
        raise HTTPException(
            status_code=503,
            detail=("Bitrix event endpoint is disabled"),
        )

    body = await request.body()

    try:
        result = await ingest_bitrix_event(
            database_path=(settings.database_path),
            application_token=(settings.bitrix_event_application_token),
            content_type=(
                request.headers.get(
                    "content-type",
                    "",
                )
            ),
            body=body,
            expected_member_id=(settings.bitrix_event_member_id),
            expected_domain=(settings.bitrix_event_domain),
            max_body_bytes=(settings.bitrix_event_max_body_bytes),
        )

    except BitrixRealtimeEventError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
        ) from exc

    return {
        "status": "accepted",
        "event_id": (result.inbox_id),
        "duplicate": (not result.inserted),
        "event": (result.event_name),
        "entity_type": (result.entity_type),
        "entity_id": (result.entity_id or None),
        "call_id": (result.call_id or None),
    }


@app.post(
    "/api/v1/agent-runs",
    response_model=AgentResponse,
    dependencies=[Depends(authorize)],
)
async def run_agent(
    request: AgentRequest,
    settings: SettingsDependency,
) -> AgentResponse:
    try:
        result = await execute_agent(request.message, settings)
    except AgentExecutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc

    return AgentResponse(
        run_id=get_correlation_id(),
        status="completed",
        answer=result.answer,
        agent=result.agent,
    )
