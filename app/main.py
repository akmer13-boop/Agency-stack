import secrets

from agents import Runner
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.agents.orchestrator import orchestrator
from app.config import Settings, get_settings


app = FastAPI(title="Agency Stack", version="0.1.0")


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

    result = await Runner.run(
        starting_agent=orchestrator,
        input=request.message,
        max_turns=settings.agent_max_turns,
    )
    return AgentResponse(answer=str(result.final_output), agent=result.last_agent.name)
