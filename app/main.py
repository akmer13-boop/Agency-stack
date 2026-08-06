import logging
import secrets

from agents import Runner, set_default_openai_key, set_tracing_disabled
from fastapi import Depends, FastAPI, Header, HTTPException, status
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


logger = logging.getLogger(__name__)
settings = get_settings()

if settings.openai_api_key:
    set_default_openai_key(settings.openai_api_key)

set_tracing_disabled(not settings.openai_tracing_enabled)

app = FastAPI(title=settings.app_name, version=settings.app_version)


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class AgentResponse(BaseModel):
    answer: str
    agent: str


def authorize(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
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
async def health(settings: Settings = Depends(get_settings)) -> dict[str, str | bool]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "crm_write_enabled": settings.allow_crm_write,
    }


@app.post(
    "/api/v1/agent-runs",
    response_model=AgentResponse,
    dependencies=[Depends(authorize)],
)
async def run_agent(
    request: AgentRequest,
    settings: Settings = Depends(get_settings),
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
        logger.warning("OpenAI authentication failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAI rejected the configured API credentials",
        ) from exc
    except PermissionDeniedError as exc:
        error_code = getattr(exc, "code", None)
        logger.warning("OpenAI permission denied: code=%s", error_code)

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
        logger.warning("OpenAI rate limit reached")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="OpenAI rate limit or account quota was reached",
        ) from exc
    except APIConnectionError as exc:
        logger.warning("Unable to connect to OpenAI API")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to connect to OpenAI API",
        ) from exc
    except APIStatusError as exc:
        logger.warning(
            "OpenAI API returned an error: status=%s request_id=%s",
            exc.status_code,
            getattr(exc, "request_id", None),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenAI API returned an unexpected error",
        ) from exc

    return AgentResponse(answer=str(result.final_output), agent=result.last_agent.name)
