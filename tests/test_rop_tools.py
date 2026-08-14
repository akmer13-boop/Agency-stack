from app.config import Settings
from app.domain import AgentRoute, UserRole
from app.services.agent_runner import _is_weekend_lead_query, _prepare_agent
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
        "get_rop_lead_response_evidence",
        "get_rop_lead_response_trend",
        "get_rop_first_response_policy",
        "get_rop_business_policy_status",
        "get_rop_actor_resolution",
        "get_rop_data_gap_diagnostics",
        "get_rop_fact_quality",
        "get_rop_management_facts",
        "get_rop_weekend_leads",
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
    assert "get_rop_lead_response_evidence" in names
    assert "get_rop_lead_response_trend" in names
    assert "get_rop_first_response_policy" in names
    assert "get_rop_business_policy_status" in names
    assert "get_rop_actor_resolution" in names
    assert "get_rop_data_gap_diagnostics" in names
    assert "get_rop_fact_quality" in names
    assert "get_rop_management_facts" in names
    assert "get_rop_weekend_leads" in names


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
    assert "используй get_rop_lead_response_evidence" in agent.instructions
    assert "используй get_rop_lead_response_trend" in agent.instructions
    assert "статистически значимым улучшением/ухудшением" in agent.instructions
    assert "не First Response SLA" in agent.instructions
    assert "Рабочие часы, выходные, праздники, reassignment" in agent.instructions
    assert "используй get_rop_first_response_policy" in agent.instructions
    assert "Статус READY означает только готовность конфигурации" in agent.instructions
    assert "ID→ФИО" in agent.instructions


def test_manager_agent_routes_weekend_customer_question_without_clarification() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "обязательно используй get_rop_weekend_leads" in agent.instructions
    assert "Не спрашивай даты или часовой пояс" in agent.instructions
    assert "get_rop_lead_activities" in agent.instructions
    assert "не пиши 'после подтверждения запущу" in agent.instructions

    assert _is_weekend_lead_query("Сколько лидов пришло за выходные и как их менеджеры отработали?")
    assert _is_weekend_lead_query("Как обработали лиды за субботу и воскресенье?")
    assert not _is_weekend_lead_query("Как распределить менеджеров на выходные?")


def test_employee_agent_does_not_receive_rop_tools() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.EMPLOYEE, settings)
    assert not agent.tools


def test_manager_agent_routes_objective_manager_facts_to_policy_free_tool() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "используй get_rop_management_facts" in agent.instructions
    assert "policy-free fact layer" in agent.instructions
    assert "pending_business_approval" in agent.instructions
    assert "не рассчитывай его самостоятельно" in agent.instructions


def test_manager_agent_routes_data_coverage_questions_to_quality_tool() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "используй get_rop_fact_quality" in agent.instructions
    assert "не придумывай минимальный допустимый процент" in agent.instructions
    assert "различай наличие исходных данных" in agent.instructions


def test_manager_agent_routes_business_policy_questions_to_registry() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(AgentRoute.SALES_MANAGER, UserRole.MANAGER, settings)
    assert isinstance(agent.instructions, str)
    assert "используй get_rop_business_policy_status" in agent.instructions
    assert "approved означает только решение" in agent.instructions
    assert "operational=yes" in agent.instructions
    assert "configuration READY" in agent.instructions


def test_manager_agent_routes_exact_gap_questions_to_diagnostics() -> None:
    settings = Settings(_env_file=None)
    agent = _prepare_agent(
        AgentRoute.SALES_MANAGER,
        UserRole.MANAGER,
        settings,
    )
    assert isinstance(agent.instructions, str)
    assert "используй get_rop_actor_resolution" in agent.instructions
    assert "special_actor_candidate" in agent.instructions
    assert "unresolved_actor" in agent.instructions
    assert "используй get_rop_data_gap_diagnostics" in agent.instructions
    assert "не обещай автоматическое исправление CRM" in agent.instructions
