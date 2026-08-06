import logging
import secrets
import time
import uuid
from typing import Annotated, Literal

from agents import Runner, set_default_openai_key, set_tracing_disabled
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, Field

from app.agents.orchestrator import orchestrator
from app.config import Settings, get_settings
from app.observability import (
    configure_logging,
    correlation_id_var,
    get_correlation_id,
)


configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
SettingsDependency = Annotated[Settings, Depends(get_settings)]
AuthorizationHeader = Annotated[str | None, Header()]

if settings.openai_api_key:
    set_default_openai_key(settings.openai_api_key)

set_tracing_disabled(not settings.openai_tracing_enabled)

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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGENT_API_TOKEN is not configured",
        )

    expected = f"Bearer {settings.agent_api_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@app.get("/health")
async def health(settings: SettingsDependency) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "version": settings.app_version,
        "openai_configured": bool(settings.openai_api_key),
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
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENAI_API_KEY is not configured",
        )

    try:
        result = await Runner.run(
            starting_agent=orchestrator,
            input=request.message,
            max_turns=settings.agent_max_turns,
        )
    except AuthenticationError as exc:
        logger.warning("OpenAI authentication failed", extra={"event": "openai_error"})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAI rejected the configured API credentials",
        ) from exc
    except PermissionDeniedError as exc:
        error_code = getattr(exc, "code", None)
        logger.warning(
            "OpenAI permission denied: code=%s",
            error_code,
            extra={"event": "openai_error"},
        )

        if error_code == "unsupported_country_region_territory":
            detail = (
                "OpenAI API is unavailable from the current server network region. "
                "Run Agency Stack from a supported country or contact OpenAI support "
                "if the server is already located in one."
            )
        else:
            detail = "OpenAI denied access to the requested resource"

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        ) from exc
    except RateLimitError as exc:
        logger.warning("OpenAI rate limit reached", extra={"event": "openai_error"})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="OpenAI rate limit or account quota was reached",
        ) from exc
    except APIConnectionError as exc:
        logger.warning("Unable to connect to OpenAI API", extra={"event": "openai_error"})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to connect to OpenAI API",
        ) from exc
    except APIStatusError as exc:
        logger.warning(
            "OpenAI API returned an error: status=%s request_id=%s",
            exc.status_code,
            getattr(exc, "request_id", None),
            extra={"event": "openai_error"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAI API returned an unexpected error",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected agent execution failure", extra={"event": "agent_error"})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent execution failed",
        ) from exc

    logger.info(
        "Agent run completed",
        extra={"event": "agent_run", "agent": result.last_agent.name},
    )
    return AgentResponse(
        run_id=get_correlation_id(),
        status="completed",
        answer=str(result.final_output),
        agent=result.last_agent.name,
    )
