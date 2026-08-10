import pytest

from app.domain import AgentRoute
from app.services.routing import route_message


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Бот не работает после запуска", AgentRoute.TECHNICAL),
        (
            "Сколько лидов пришло за выходные и как их менеджеры отработали?",
            AgentRoute.DEAL_ANALYST,
        ),
        ("Как оформить договор?", AgentRoute.KNOWLEDGE),
        ("Какие сделки зависли без движения?", AgentRoute.DEAL_ANALYST),
        ("Покажи прогноз выручки и план продаж", AgentRoute.SALES_MANAGER),
        ("Покажи показатели за текущий месяц", AgentRoute.SALES_MANAGER),
        ("Что по продажам сегодня?", AgentRoute.SALES_MANAGER),
        ("Расскажи, чем ты можешь помочь", AgentRoute.ORCHESTRATOR),
    ],
)
def test_route_message(message: str, expected: AgentRoute) -> None:
    assert route_message(message) is expected
