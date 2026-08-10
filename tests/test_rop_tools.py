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
        "get_rop_sla",
        "get_rop_cycle_time",
        "get_rop_focus",
        "get_rop_deal",
        "get_rop_deal_activity",
        "get_rop_leads",
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
    assert "get_rop_sla" in names
    assert "get_rop_cycle_time" in names
    assert "get_rop_focus" in names
    assert "get_rop_deal" in names
    assert "get_rop_deal_activity" in names
    assert "get_rop_leads" in names


def test_manager_agent_has_grounded_deadline_guardrails() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "Не придумывай новые числовые дедлайны" in agent.instructions
    assert "Не предлагай 'продлить SLA'" in agent.instructions
    assert "называй это ручной проверкой" in agent.instructions


def test_manager_agent_does_not_infer_missing_follow_up_from_stage_age() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "само по себе не доказывает отсутствие follow-up" in agent.instructions
    assert "не пиши гипотезу 'follow-up не было'" in agent.instructions
    assert "универсальные причины вроде цены" in agent.instructions
    assert "три независимых сигнала" in agent.instructions
    assert "не соответствие отдельному SLA коммуникационной паузы" in agent.instructions


def test_manager_agent_uses_recent_activity_tool_without_export() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "обязательно используй get_rop_deal_activity" in agent.instructions
    assert "За последнюю неделю" in agent.instructions
    assert "rolling 7 дней" in agent.instructions
    assert "Не проси CSV/JSON" in agent.instructions
    assert "разные величины" in agent.instructions


def test_manager_agent_verifies_unconfirmed_pipeline_before_follow_up() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "первым управленческим действием рекомендуй подтвердить" in agent.instructions
    assert "неподтверждённым pipeline" in agent.instructions
    assert "Не называй сделку мёртвой" in agent.instructions


def test_manager_agent_routes_lead_questions_to_lead_intelligence() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "обязательно используй get_rop_leads" in agent.instructions
    assert "Не подменяй lead-focused вопрос общим get_rop_period" in agent.instructions
    assert "First-response SLA по лидам не выводи" in agent.instructions
    assert "ID→ФИО" in agent.instructions


def test_employee_agent_does_not_receive_rop_tools() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.EMPLOYEE, settings)
    assert not agent.tools
