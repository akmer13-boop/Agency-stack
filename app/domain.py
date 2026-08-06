from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    OBSERVER = "observer"

    @property
    def label(self) -> str:
        return {
            UserRole.ADMIN: "Администратор",
            UserRole.MANAGER: "Руководитель",
            UserRole.EMPLOYEE: "Сотрудник",
            UserRole.OBSERVER: "Наблюдатель",
        }[self]


class AgentRoute(StrEnum):
    ORCHESTRATOR = "orchestrator"
    SALES_MANAGER = "sales_manager"
    DEAL_ANALYST = "deal_analyst"
    KNOWLEDGE = "knowledge"
    TECHNICAL = "technical"

    @property
    def label(self) -> str:
        return {
            AgentRoute.ORCHESTRATOR: "Главный оркестратор",
            AgentRoute.SALES_MANAGER: "ИИ-РОП",
            AgentRoute.DEAL_ANALYST: "Аналитик сделок",
            AgentRoute.KNOWLEDGE: "База знаний",
            AgentRoute.TECHNICAL: "Технический администратор",
        }[self]
