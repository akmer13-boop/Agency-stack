from datetime import UTC, datetime
from types import SimpleNamespace

from app.config import Settings
from app.services.rop_b2c_first_response_truth import (
    B2CFirstResponseBreachTruth,
)
from app.services.rop_b2c_problem_cards import (
    format_b2c_problem_cards_for_ai,
    format_b2c_today_focus_for_ai,
)
from app.services.rop_b2c_stage_sla_truth import (
    StageSlaDealTruth,
)

WEBHOOK = "https://b24.example.test/rest/7/supersecretcode/"
CUTOFF = datetime(2026, 8, 22, 12, 56, tzinfo=UTC)


def _lead(
    lead_id: int,
    *,
    manager_id: str | None,
    elapsed_minutes: int,
) -> B2CFirstResponseBreachTruth:
    return B2CFirstResponseBreachTruth(
        lead_id=lead_id,
        manager_id=manager_id,
        created_at=datetime(2026, 8, 21, 7, 0, tzinfo=UTC),
        response_at=(
            datetime(2026, 8, 21, 7, elapsed_minutes, tzinfo=UTC)
            if manager_id is not None
            else None
        ),
        deadline_at=datetime(2026, 8, 21, 7, 15, tzinfo=UTC),
        threshold_business_seconds=15 * 60,
        elapsed_business_seconds=elapsed_minutes * 60,
    )


def _dashboard():
    first_response = SimpleNamespace(
        breach=3,
        breach_by_manager=(("7", 2),),
        unattributed_breaches=1,
        breach_leads=(
            _lead(101, manager_id="7", elapsed_minutes=45),
            _lead(102, manager_id="7", elapsed_minutes=30),
            _lead(999, manager_id=None, elapsed_minutes=60),
        ),
    )
    deal = StageSlaDealTruth(
        deal_id=7040,
        stage_id="C7:EXECUTING",
        stage_label="КП отправлено",
        status="ATTENTION",
        manager_id="7",
        manager_name="Ольга Попкова",
        stage_entered_at=datetime(2026, 8, 20, 7, 0, tzinfo=UTC),
        anchor_at=datetime(2026, 8, 20, 7, 0, tzinfo=UTC),
        deadline_at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        last_qualifying_activity_at=None,
        last_qualifying_activity_kind="",
        blocker_reason="",
    )
    stage_sla = SimpleNamespace(
        attention=1,
        deals=(deal,),
    )
    return SimpleNamespace(
        cutoff_at=CUTOFF,
        first_response=first_response,
        stage_sla=stage_sla,
        managers=(
            SimpleNamespace(
                manager_id="7",
                manager_name="Ольга Попкова",
            ),
        ),
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        bitrix24_webhook_url=WEBHOOK,
        rop_timezone="Europe/Moscow",
    )


def test_problem_cards_group_exact_entities_and_keep_secret_out() -> None:
    text = format_b2c_problem_cards_for_ai(
        _dashboard(),
        _settings(),
        scope="all",
        max_managers=5,
        cards_per_manager=5,
    )

    assert "Менеджер: Ольга Попкова (ID 7)" in text
    assert "подтверждённых нарушений первого ответа: 2" in text
    assert "Лид #101" in text
    assert (
        "[Лид #101](https://b24.example.test/crm/lead/details/101/)"
        in text
    )
    assert "https://b24.example.test/crm/lead/details/101/" in text
    assert "https://b24.example.test/crm/lead/details/102/" in text
    assert "КП отправлено" in text
    assert (
        "[Сделка #7040](https://b24.example.test/crm/deal/details/7040/)"
        in text
    )
    assert "https://b24.example.test/crm/deal/details/7040/" in text
    assert "без безопасной атрибуции: 1" in text
    assert "crm/lead/details/999" not in text
    assert "supersecretcode" not in text
    assert "/rest/" not in text
    assert "BITRIX WRITES = NONE" in text


def test_lead_scope_does_not_mislabel_stage_deals_as_leads() -> None:
    text = format_b2c_problem_cards_for_ai(
        _dashboard(),
        _settings(),
        scope="leads",
        manager_id="7",
        cards_per_manager=1,
    )

    assert "Лид #101" in text
    assert "Лид #102" not in text
    assert "Сделка #7040" not in text
    assert "crm/deal/details" not in text
    assert "показано 1 из 2" in text


def test_problem_cards_without_bitrix_config_do_not_invent_links() -> None:
    settings = Settings(
        _env_file=None,
        bitrix24_webhook_url="",
        rop_timezone="Europe/Moscow",
    )

    text = format_b2c_problem_cards_for_ai(
        _dashboard(),
        settings,
        scope="leads",
        manager_id="7",
        cards_per_manager=1,
    )

    assert "ссылка недоступна: BITRIX24_WEBHOOK_URL не настроен" in text
    assert "https://" not in text


def test_today_focus_is_clean_b2c_list_with_clickable_deal_number() -> None:
    text = format_b2c_today_focus_for_ai(
        _dashboard(),
        _settings(),
        limit=5,
    )

    assert "ИИ-РОП · B2C · что проверить сегодня" in text
    assert "текущий B2C backlog" in text
    assert (
        "[Сделка #7040](https://b24.example.test/crm/deal/details/7040/)"
        in text
    )
    assert "КП отправлено" in text
    assert "менеджер: Ольга Попкова" in text
    assert "почему: срок Stage SLA прошёл" in text
    assert "не доказательство отсутствия коммуникации" in text
    assert "C7:EXECUTING" not in text
    assert "Продажи B2B" not in text
    assert "RUB" not in text
    assert "КРИТИЧНО" not in text
    assert "BITRIX WRITES = NONE" in text
