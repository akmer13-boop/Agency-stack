from __future__ import annotations

from urllib.parse import quote

from app.config import Settings

SUPPORTED_PROXY_TYPES = frozenset({"http", "https", "socks5", "socks5h"})


class ProxyConfigurationError(ValueError):
    """Raised when the outbound proxy settings are incomplete or unsafe."""


def build_proxy_url(settings: Settings, *, remote_dns: bool = True) -> str | None:
    """Build a proxy URL without exposing it through logs or Settings repr."""
    proxy_type = settings.proxy_type.strip().lower()
    host = settings.proxy_host.strip()
    port = settings.proxy_port
    username = settings.proxy_username.strip()
    password = settings.proxy_password

    any_proxy_setting = bool(proxy_type or host or port or username or password)
    if not any_proxy_setting:
        return None

    if proxy_type not in SUPPORTED_PROXY_TYPES:
        raise ProxyConfigurationError(
            "PROXY_TYPE must be one of: http, https, socks5, socks5h"
        )
    if not host:
        raise ProxyConfigurationError("PROXY_HOST is required when proxy is configured")
    if port <= 0:
        raise ProxyConfigurationError("PROXY_PORT must be set when proxy is configured")
    if bool(username) != bool(password):
        raise ProxyConfigurationError(
            "PROXY_USERNAME and PROXY_PASSWORD must be both set or both empty"
        )

    scheme = proxy_type
    if proxy_type in {"socks5", "socks5h"}:
        scheme = "socks5h" if remote_dns else "socks5"

    credentials = ""
    if username and password:
        credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@"

    formatted_host = host
    if ":" in host and not host.startswith("["):
        formatted_host = f"[{host}]"

    return f"{scheme}://{credentials}{formatted_host}:{port}"
