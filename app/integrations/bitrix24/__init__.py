"""Safe read-only Bitrix24 integration."""

from app.integrations.bitrix24.client import (
    BITRIX24_READ_ONLY_METHODS,
    Bitrix24ConfigurationError,
    Bitrix24ReadOnlyClient,
    Bitrix24ReadOnlyViolation,
    Bitrix24RequestError,
)

__all__ = [
    "BITRIX24_READ_ONLY_METHODS",
    "Bitrix24ConfigurationError",
    "Bitrix24ReadOnlyClient",
    "Bitrix24ReadOnlyViolation",
    "Bitrix24RequestError",
]
