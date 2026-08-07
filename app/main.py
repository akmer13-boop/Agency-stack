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
async def health(settings: SettingsDependency) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "version": settings.app_version,
        "openai_configured": bool(settings.openai_api_key),
        "telegram_configured": bool(settings.telegram_bot_token),
        "crm_write_enabled": settings.allow_crm_write,
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
