from agents import set_default_openai_key, set_tracing_disabled

from app.config import Settings


def configure_openai_runtime(settings: Settings) -> None:
    """Configure the shared OpenAI Agents SDK runtime for the current process."""
    if settings.openai_api_key:
        set_default_openai_key(settings.openai_api_key)

    set_tracing_disabled(not settings.openai_tracing_enabled)
