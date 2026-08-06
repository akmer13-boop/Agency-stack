import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.services.agent_runner import AgentRunResult

TEST_AGENT_TOKEN = "test-agent-token"


@pytest.fixture
def configured_settings() -> Settings:
    return Settings(
        environment="test",
        openai_api_key="test-openai-key",
        agent_api_token=TEST_AGENT_TOKEN,
        openai_tracing_enabled=False,
        allow_crm_write=False,
    )


@pytest.fixture
def client(configured_settings: Settings):
    app.dependency_overrides[get_settings] = lambda: configured_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_reports_safe_runtime_state(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "environment": "test",
        "version": "0.2.0",
        "openai_configured": True,
        "telegram_configured": False,
        "crm_write_enabled": False,
    }
    assert response.headers["X-Correlation-ID"]


def test_agent_endpoint_rejects_missing_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agent-runs",
        json={"message": "Проверка"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_agent_endpoint_rejects_missing_openai_key() -> None:
    settings = Settings(
        environment="test",
        openai_api_key="",
        agent_api_token=TEST_AGENT_TOKEN,
    )
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/v1/agent-runs",
                headers={"Authorization": f"Bearer {TEST_AGENT_TOKEN}"},
                json={"message": "Проверка"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "OPENAI_API_KEY is not configured"}


def test_agent_endpoint_returns_structured_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_agent(_message: str, _settings: Settings) -> AgentRunResult:
        return AgentRunResult(
            answer="Оркестратор работает",
            agent="Agency Stack Orchestrator",
        )

    monkeypatch.setattr("app.main.execute_agent", fake_execute_agent)

    correlation_id = "test-run-123"
    response = client.post(
        "/api/v1/agent-runs",
        headers={
            "Authorization": f"Bearer {TEST_AGENT_TOKEN}",
            "X-Correlation-ID": correlation_id,
        },
        json={"message": "Представься"},
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.json() == {
        "run_id": correlation_id,
        "status": "completed",
        "answer": "Оркестратор работает",
        "agent": "Agency Stack Orchestrator",
    }
