from agents import set_default_openai_client, set_default_openai_key, set_tracing_disabled
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from app.config import Settings
from app.proxy import build_proxy_url


def configure_openai_runtime(settings: Settings) -> None:
    """Configure the shared OpenAI Agents SDK runtime for the current process."""
    proxy_url = build_proxy_url(settings, remote_dns=True)

    if settings.openai_api_key:
        if proxy_url:
            client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                http_client=DefaultAsyncHttpxClient(proxy=proxy_url),
            )
            set_default_openai_client(client)
        else:
            set_default_openai_key(settings.openai_api_key)

    set_tracing_disabled(not settings.openai_tracing_enabled)
