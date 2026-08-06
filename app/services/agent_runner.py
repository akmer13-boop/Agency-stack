import logging
from dataclasses import dataclass

from agents import Runner
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)

from app.agents.orchestrator import orchestrator
from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    answer: str
    agent: str


class AgentExecutionError(RuntimeError):
    def __init__(self, public_message: str, *, status_code: int) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code


async def execute_agent(message: str, settings: Settings) -> AgentRunResult:
    if not settings.openai_api_key:
        raise AgentExecutionError(
            "OPENAI_API_KEY is not configured",
            status_code=503,
        )

    try:
        result = await Runner.run(
            starting_agent=orchestrator,
            input=message,
            max_turns=settings.agent_max_turns,
        )
    except AuthenticationError as exc:
        logger.warning("OpenAI authentication failed", extra={"event": "openai_error"})
        raise AgentExecutionError(
            "OpenAI rejected the configured API credentials",
            status_code=502,
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

        raise AgentExecutionError(detail, status_code=503) from exc
    except RateLimitError as exc:
        logger.warning("OpenAI rate limit reached", extra={"event": "openai_error"})
        raise AgentExecutionError(
            "OpenAI rate limit or account quota was reached",
            status_code=429,
        ) from exc
    except APIConnectionError as exc:
        logger.warning("Unable to connect to OpenAI API", extra={"event": "openai_error"})
        raise AgentExecutionError(
            "Unable to connect to OpenAI API",
            status_code=502,
        ) from exc
    except APIStatusError as exc:
        logger.warning(
            "OpenAI API returned an error: status=%s request_id=%s",
            exc.status_code,
            getattr(exc, "request_id", None),
            extra={"event": "openai_error"},
        )
        raise AgentExecutionError(
            "OpenAI API returned an unexpected error",
            status_code=502,
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected agent execution failure", extra={"event": "agent_error"})
        raise AgentExecutionError("Agent execution failed", status_code=500) from exc

    logger.info(
        "Agent run completed",
        extra={"event": "agent_run", "agent": result.last_agent.name},
    )
    return AgentRunResult(
        answer=str(result.final_output),
        agent=result.last_agent.name,
    )
