from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    app_name: str = "Agency Stack"
    app_version: str = "0.1.0"

    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = "gpt-5-mini"
    openai_tracing_enabled: bool = False
    agent_api_token: str = Field(default="", repr=False)
    agent_max_turns: int = Field(default=6, ge=1, le=20)

    allow_crm_write: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
