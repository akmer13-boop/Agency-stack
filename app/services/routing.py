from app.domain import AgentRoute

_TECHNICAL_KEYWORDS = (
    "бот",
    "ошибка",
    "не работает",
    "сервер",
    "api",
    "токен",
    "интеграц",
    "настрой",
    "доступ",
    "авторизац",
)

_KNOWLEDGE_KEYWORDS = (
    "база знаний",
    "инструкц",
    "регламент",
    "как оформить",
    "как сделать",
    "договор",
    "правил",
    "шаблон",
    "процедур",
)

_DEAL_KEYWORDS = (
    "проблемн",
    "сделк",
    "лид",
    "завис",
    "просроч",
    "следующ",
    "без движения",
    "этап сдел",
)

_SALES_MANAGER_KEYWORDS = (
    "роп",
    "продаж",
    "план продаж",
    "выручк",
    "конверси",
    "воронк",
    "менеджер",
    "средний чек",
    "прогноз",
    "pipeline",
    "пайплайн",
    "показател",
    "kpi",
    "сводк",
    "сегодня",
    "недел",
    "месяц",
    "текущий период",
)


def route_message(message: str) -> AgentRoute:
    normalized = message.casefold()

    if any(keyword in normalized for keyword in _TECHNICAL_KEYWORDS):
        return AgentRoute.TECHNICAL
    if any(keyword in normalized for keyword in _KNOWLEDGE_KEYWORDS):
        return AgentRoute.KNOWLEDGE
    if any(keyword in normalized for keyword in _DEAL_KEYWORDS):
        return AgentRoute.DEAL_ANALYST
    if any(keyword in normalized for keyword in _SALES_MANAGER_KEYWORDS):
        return AgentRoute.SALES_MANAGER
    return AgentRoute.ORCHESTRATOR
