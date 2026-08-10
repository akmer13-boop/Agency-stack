import pytest

from app.config import Settings
from app.proxy import ProxyConfigurationError, build_proxy_url
from app.services.bitrix24_service import build_bitrix24_client


def make_settings(**overrides) -> Settings:
    defaults = {
        "_env_file": None,
        "bitrix24_webhook_url": "https://b24.example.test/rest/7/supersecretcode/",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_proxy_is_disabled_when_settings_are_empty() -> None:
    settings = make_settings()

    assert settings.proxy_configured is False
    assert build_proxy_url(settings) is None


def test_socks5_proxy_supports_ip_whitelist_without_credentials() -> None:
    settings = make_settings(
        proxy_type="socks5",
        proxy_host="proxy.example.test",
        proxy_port=1080,
    )

    assert settings.proxy_configured is True
    assert settings.proxy_uses_credentials is False
    assert build_proxy_url(settings, remote_dns=True) == (
        "socks5h://proxy.example.test:1080"
    )
    assert build_proxy_url(settings, remote_dns=False) == (
        "socks5://proxy.example.test:1080"
    )


def test_proxy_credentials_are_url_encoded() -> None:
    settings = make_settings(
        proxy_type="socks5",
        proxy_host="proxy.example.test",
        proxy_port=1080,
        proxy_username="user@example",
        proxy_password="p@ss word",
    )

    assert build_proxy_url(settings) == (
        "socks5h://user%40example:p%40ss%20word@proxy.example.test:1080"
    )


def test_partial_proxy_credentials_are_rejected() -> None:
    settings = make_settings(
        proxy_type="socks5",
        proxy_host="proxy.example.test",
        proxy_port=1080,
        proxy_username="user",
    )

    with pytest.raises(ProxyConfigurationError, match="both set or both empty"):
        build_proxy_url(settings)


def test_bitrix24_factory_receives_shared_proxy() -> None:
    settings = make_settings(
        proxy_type="socks5",
        proxy_host="proxy.example.test",
        proxy_port=1080,
    )

    client = build_bitrix24_client(settings)

    assert client._proxy_url == "socks5h://proxy.example.test:1080"
