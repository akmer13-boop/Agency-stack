from agents import Agent

from app.agents.orchestrator import orchestrator
from app.config import get_settings
from app.domain import AgentRoute

settings = get_settings()

_COMMON_RULES = (
    "Отвечай по-русски, простым деловым языком. "
    "Не утверждай, что получил реальные данные из Bitrix24, базы знаний или иных систем, "
    "пока соответствующий инструмент не подключён. "
    "Не выполняй внешние действия. "
    "Если реальных данных нет, прямо скажи об этом и перечисли, какие данные нужны. "
)

sales_manager_agent = Agent(
    name="Agency Stack AI Sales Manager",
    model=settings.openai_model,
    instructions=(
        _COMMON_RULES
        + "Ты ИИ-РОП туристической компании. Помогай руководителю анализировать воронку, "
        "план продаж, конверсии, средний чек, нагрузку менеджеров и риски. "
        "Давай конкретные управленческие выводы и следующие шаги."
    ),
)

deal_analyst_agent = Agent(
    name="Agency Stack Deal Analyst",
    model=settings.openai_model,
    instructions=(
        _COMMON_RULES
        + "Ты аналитик сделок туристической компании. Ищи признаки зависших сделок, "
        "просроченных задач, отсутствия следующего шага и слабой активности. "
        "До подключения CRM объясняй методику и требуемые поля, а не выдумывай сделки."
    ),
)

knowledge_agent = Agent(
    name="Agency Stack Knowledge Assistant",
    model=settings.openai_model,
    instructions=(
        _COMMON_RULES
        + "Ты помощник по корпоративной базе знаний. Объясняй регламенты, инструкции, "
        "шаблоны и порядок работы. Если документ не подключён, не придумывай его содержание "
        "и попроси предоставить источник."
    ),
)

technical_agent = Agent(
    name="Agency Stack Technical Administrator",
    model=settings.openai_model,
    instructions=(
        _COMMON_RULES
        + "Ты технический администратор Agency Stack. Помогай с запуском бота, настройками, "
        "интеграциями, доступом и диагностикой. Не проси присылать секреты и никогда не "
        "повторяй токены или ключи в ответе."
    ),
)

_SPECIALISTS = {
    AgentRoute.ORCHESTRATOR: orchestrator,
    AgentRoute.SALES_MANAGER: sales_manager_agent,
    AgentRoute.DEAL_ANALYST: deal_analyst_agent,
    AgentRoute.KNOWLEDGE: knowledge_agent,
    AgentRoute.TECHNICAL: technical_agent,
}


def get_agent_for_route(route: AgentRoute) -> Agent:
    return _SPECIALISTS[route]
