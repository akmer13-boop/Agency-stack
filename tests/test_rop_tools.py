from app.config import Settings
from app.domain import AgentRoute, UserRole
from app.services.agent_runner import _prepare_agent
from app.services.rop_catalog import category_label, stage_label
from app.services.rop_tools import build_rop_function_tools


def test_tourism_catalog_labels_known_ids() -> None:
    assert category_label("7") == "Продажи B2C (ID 7)"
    assert stage_label("C7:EXECUTING") == "КП отправлено (C7:EXECUTING)"
    assert category_label("999") == "Воронка ID 999 (ID 999)"
    assert stage_label("UNKNOWN") == "UNKNOWN"


def test_rop_function_tools_are_read_only_analytics_surface() -> None:
    settings = Settings(_env_file=None)
    tools = build_rop_function_tools(settings)
    names = {tool.name for tool in tools}
    assert names == {
        "get_rop_period",
        "get_rop_pipeline",
        "get_rop_funnel",
        "get_rop_risks",
        "get_rop_losses",
        "get_rop_stage_aging",
        "get_rop_managers",
    }


def test_manager_agent_receives_rop_tools() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    names = {tool.name for tool in agent.tools}
    assert "get_rop_period" in names
    assert "get_rop_pipeline" in names
    assert "get_rop_funnel" in names
    assert "get_rop_risks" in names
    assert "get_rop_losses" in names
    assert "get_rop_stage_aging" in names
    assert "get_rop_managers" in names


def test_employee_agent_does_not_receive_rop_tools() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.EMPLOYEE, settings)
    assert not agent.tools
