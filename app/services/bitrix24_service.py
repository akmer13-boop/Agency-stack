from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.integrations.bitrix24 import (
    Bitrix24ConfigurationError,
    Bitrix24RequestError,
)
from app.integrations.bitrix24.compatible_client import CompatibleBitrix24ReadOnlyClient
from app.proxy import build_proxy_url


@dataclass(frozen=True, slots=True)
class Bitrix24ConnectionStatus:
    configured: bool
    connected: bool
    portal_host: str | None = None
    webhook_user_id: str | None = None
    webhook_user_is_admin: bool | None = None
    error: str | None = None


def build_bitrix24_client(settings: Settings) -> CompatibleBitrix24ReadOnlyClient:
    return CompatibleBitrix24ReadOnlyClient(
        settings.bitrix24_webhook_url,
        timeout_seconds=settings.bitrix24_timeout_seconds,
        verify_ssl=settings.bitrix24_verify_ssl,
        max_pages=settings.bitrix24_max_pages,
        proxy_url=build_proxy_url(settings, remote_dns=True),
    )


async def check_bitrix24_connection(settings: Settings) -> Bitrix24ConnectionStatus:
    if not settings.bitrix24_configured:
        return Bitrix24ConnectionStatus(
            configured=False,
            connected=False,
            error="BITRIX24_WEBHOOK_URL is not configured",
        )

    try:
        client = build_bitrix24_client(settings)
        profile = await client.profile()
    except (Bitrix24ConfigurationError, Bitrix24RequestError) as exc:
        return Bitrix24ConnectionStatus(
            configured=True,
            connected=False,
            error=str(exc),
        )

    return Bitrix24ConnectionStatus(
        configured=True,
        connected=True,
        portal_host=client.portal_host,
        webhook_user_id=str(profile.get("ID")) if profile.get("ID") is not None else None,
        webhook_user_is_admin=bool(profile.get("ADMIN")),
    )
