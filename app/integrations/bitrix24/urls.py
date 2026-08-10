from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from app.integrations.bitrix24.client import normalize_webhook_url

BitrixEntityType = Literal["deal", "lead"]


class BitrixEntityUrlError(ValueError):
    """Raised when a Bitrix entity URL cannot be constructed safely."""


def portal_origin_from_webhook(webhook_url: str) -> str:
    """Return only the public portal origin, never the REST webhook path/secret."""

    normalized = normalize_webhook_url(webhook_url)
    parsed = urlsplit(normalized)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _entity_id(value: str | int) -> str:
    normalized = str(value).strip()
    if not normalized.isdigit() or int(normalized) <= 0:
        raise BitrixEntityUrlError("Bitrix entity ID must be a positive integer")
    return normalized


def build_bitrix_entity_url(
    webhook_url: str,
    entity_type: BitrixEntityType,
    entity_id: str | int,
) -> str:
    """Build a secret-free browser URL for a supported Bitrix24 CRM entity."""

    segments = {
        "deal": "deal",
        "lead": "lead",
    }
    if entity_type not in segments:
        raise BitrixEntityUrlError(f"Unsupported Bitrix entity type: {entity_type}")

    normalized_id = _entity_id(entity_id)
    origin = portal_origin_from_webhook(webhook_url)
    return f"{origin}/crm/{segments[entity_type]}/details/{normalized_id}/"


def build_deal_url(webhook_url: str, deal_id: str | int) -> str:
    return build_bitrix_entity_url(webhook_url, "deal", deal_id)


def build_lead_url(webhook_url: str, lead_id: str | int) -> str:
    return build_bitrix_entity_url(webhook_url, "lead", lead_id)
