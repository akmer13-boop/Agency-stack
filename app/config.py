from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_id_list(raw_values: str, *, variable_name: str) -> frozenset[int]:
    values: set[int] = set()
    for raw_value in raw_values.split(","):
        value = raw_value.strip()
        if not value:
            continue
        try:
            values.add(int(value))
        except ValueError as exc:
            raise ValueError(
                f"{variable_name} must contain comma-separated integers"
            ) from exc
    return frozenset(values)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    app_name: str = "Agency Stack"
    app_version: str = "0.3.4"

    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = "gpt-5-mini"
    openai_tracing_enabled: bool = False
    agent_api_token: str = Field(default="", repr=False)
    agent_max_turns: int = Field(default=6, ge=1, le=20)

    telegram_bot_token: str = Field(default="", repr=False)
    telegram_allowed_user_ids: str = ""
    telegram_admin_user_ids: str = ""
    telegram_manager_user_ids: str = ""
    telegram_observer_user_ids: str = ""
    telegram_max_input_chars: int = Field(default=4000, ge=100, le=20_000)
    telegram_reply_chunk_size: int = Field(default=4000, ge=500, le=4096)
    telegram_request_cooldown_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    telegram_polling_timeout_seconds: int = Field(default=30, ge=1, le=60)

    database_path: str = "data/agency_stack.db"
    conversation_history_limit: int = Field(default=12, ge=2, le=50)

    bitrix24_webhook_url: str = Field(default="", repr=False)
    bitrix24_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    bitrix24_verify_ssl: bool = True
    bitrix24_max_pages: int = Field(default=20, ge=1, le=100)
    bitrix24_deal_preview_limit: int = Field(default=20, ge=1, le=50)
    bitrix24_summary_limit: int = Field(default=500, ge=1, le=5000)
    bitrix24_demo_mode: bool = False
    bitrix24_allow_leads: bool = False
    bitrix24_lead_preview_limit: int = Field(default=20, ge=1, le=50)
    bitrix24_stale_days: int = Field(default=3, ge=1, le=365)
    bitrix24_stale_limit: int = Field(default=100, ge=1, le=1000)

    proxy_type: str = ""
    proxy_host: str = Field(default="", repr=False)
    proxy_port: int = Field(default=0, ge=0, le=65535)
    proxy_username: str = Field(default="", repr=False)
    proxy_password: str = Field(default="", repr=False)

    allow_crm_write: bool = False

    @property
    def bitrix24_configured(self) -> bool:
        return bool(self.bitrix24_webhook_url.strip())

    @property
    def proxy_configured(self) -> bool:
        return bool(
            self.proxy_type.strip()
            and self.proxy_host.strip()
            and self.proxy_port > 0
        )

    @property
    def proxy_uses_credentials(self) -> bool:
        return bool(self.proxy_username.strip() and self.proxy_password)

    @property
    def admin_telegram_user_ids(self) -> frozenset[int]:
        return _parse_id_list(
            self.telegram_admin_user_ids,
            variable_name="TELEGRAM_ADMIN_USER_IDS",
        )

    @property
    def manager_telegram_user_ids(self) -> frozenset[int]:
        return _parse_id_list(
            self.telegram_manager_user_ids,
            variable_name="TELEGRAM_MANAGER_USER_IDS",
        )

    @property
    def observer_telegram_user_ids(self) -> frozenset[int]:
        return _parse_id_list(
            self.telegram_observer_user_ids,
            variable_name="TELEGRAM_OBSERVER_USER_IDS",
        )

    @property
    def allowed_telegram_user_ids(self) -> frozenset[int]:
        explicit = _parse_id_list(
            self.telegram_allowed_user_ids,
            variable_name="TELEGRAM_ALLOWED_USER_IDS",
        )
        return (
            explicit
            | self.admin_telegram_user_ids
            | self.manager_telegram_user_ids
            | self.observer_telegram_user_ids
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
