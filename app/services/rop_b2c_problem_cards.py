from __future__ import annotations

from collections import defaultdict
from typing import Literal
from zoneinfo import ZoneInfo

from app.config import Settings
from app.integrations.bitrix24.urls import (
    build_deal_url,
    build_lead_url,
)
from app.services.rop_b2c_first_response_truth import (
    B2CFirstResponseBreachTruth,
)
from app.services.rop_b2c_mvp_dashboard import (
    B2CMvpDashboard,
    build_b2c_mvp_dashboard,
)
from app.services.rop_b2c_stage_sla_truth import (
    StageSlaDealTruth,
)

ProblemCardScope = Literal["leads", "deals", "all"]


def _minutes(seconds: int) -> int:
    return max(0, (seconds + 59) // 60)


def _entity_url(
    settings: Settings,
    entity_type: Literal["lead", "deal"],
    entity_id: int,
) -> str:
    if not settings.bitrix24_configured:
        return "ссылка недоступна: BITRIX24_WEBHOOK_URL не настроен"

    try:
        if entity_type == "lead":
            return build_lead_url(
                settings.bitrix24_webhook_url,
                entity_id,
            )
        return build_deal_url(
            settings.bitrix24_webhook_url,
            entity_id,
        )
    except ValueError:
        # Never leak a webhook URL or its secret in an error message.
        return "ссылка недоступна: некорректная конфигурация Bitrix24"


def _manager_names(
    dashboard: B2CMvpDashboard,
) -> dict[str, str]:
    names = {
        item.manager_id: item.manager_name
        for item in dashboard.managers
    }

    for deal in dashboard.stage_sla.deals:
        if deal.manager_id and deal.manager_name:
            names.setdefault(
                deal.manager_id,
                deal.manager_name,
            )

    return names


def format_b2c_problem_cards_for_ai(
    dashboard: B2CMvpDashboard,
    settings: Settings,
    *,
    scope: ProblemCardScope = "all",
    manager_id: str | None = None,
    max_managers: int = 5,
    cards_per_manager: int = 3,
) -> str:
    """Format exact B2C problem cards without client text or webhook secrets."""

    if scope not in {"leads", "deals", "all"}:
        raise ValueError("unsupported_problem_card_scope")
    if max_managers < 1 or max_managers > 10:
        raise ValueError("max_managers_out_of_range")
    if cards_per_manager < 1 or cards_per_manager > 10:
        raise ValueError("cards_per_manager_out_of_range")

    include_leads = scope in {"leads", "all"}
    include_deals = scope in {"deals", "all"}

    leads_by_manager: defaultdict[
        str,
        list[B2CFirstResponseBreachTruth],
    ] = defaultdict(list)

    if include_leads:
        for item in dashboard.first_response.breach_leads:
            if item.manager_id:
                leads_by_manager[item.manager_id].append(item)

    deals_by_manager: defaultdict[
        str,
        list[StageSlaDealTruth],
    ] = defaultdict(list)

    if include_deals:
        for item in dashboard.stage_sla.deals:
            if item.requires_attention and item.manager_id:
                deals_by_manager[item.manager_id].append(item)

    names = _manager_names(dashboard)
    candidate_ids = set(leads_by_manager) | set(deals_by_manager)

    requested_manager_id = str(manager_id or "").strip()
    if requested_manager_id:
        selected_ids = [requested_manager_id]
        hidden_managers = 0
    else:
        ordered_ids = sorted(
            candidate_ids,
            key=lambda item: (
                len(leads_by_manager[item])
                + len(deals_by_manager[item]),
                len(leads_by_manager[item]),
                len(deals_by_manager[item]),
                names.get(item, item),
            ),
            reverse=True,
        )
        selected_ids = ordered_ids[:max_managers]
        hidden_managers = max(
            0,
            len(ordered_ids) - len(selected_ids),
        )

    zone = ZoneInfo(settings.rop_timezone)
    cutoff_text = dashboard.cutoff_at.astimezone(zone).strftime(
        "%d.%m.%Y %H:%M %Z"
    )

    lines = [
        "ИИ-РОП · точные B2C-карточки для разбора",
        f"Срез: {cutoff_text}",
        "Источник: текущая truth-математика Dashboard, не rolling-агрегаты.",
    ]

    if include_leads:
        attributed = sum(
            count
            for _manager, count
            in dashboard.first_response.breach_by_manager
        )
        lines.extend(
            [
                "",
                "First Response · лиды",
                (
                    f"• нарушений: {dashboard.first_response.breach} "
                    f"· безопасно привязано к менеджеру: {attributed} "
                    f"· без безопасной атрибуции: "
                    f"{dashboard.first_response.unattributed_breaches}"
                ),
                (
                    "• нарушения без безопасной атрибуции нельзя "
                    "распределять по менеджерам"
                ),
            ]
        )

    if include_deals:
        lines.extend(
            [
                "",
                "Stage SLA · сделки",
                (
                    f"• требуют внимания: "
                    f"{dashboard.stage_sla.attention}"
                ),
                (
                    "• возраст стадии не доказывает отсутствие "
                    "звонков, сообщений или follow-up"
                ),
            ]
        )

    if not selected_ids:
        lines.extend(
            [
                "",
                "Точных карточек по выбранному срезу нет.",
                "BITRIX WRITES = NONE",
            ]
        )
        return "\n".join(lines)

    for current_manager_id in selected_ids:
        lead_items = sorted(
            leads_by_manager.get(
                current_manager_id,
                [],
            ),
            key=lambda item: (
                item.elapsed_business_seconds,
                item.lead_id,
            ),
            reverse=True,
        )
        deal_items = sorted(
            deals_by_manager.get(
                current_manager_id,
                [],
            ),
            key=lambda item: (
                item.deadline_at is None,
                item.deadline_at,
                item.deal_id,
            ),
        )

        manager_name = names.get(
            current_manager_id,
            f"Менеджер #{current_manager_id}",
        )
        lines.extend(
            [
                "",
                f"Менеджер: {manager_name} (ID {current_manager_id})",
            ]
        )

        if include_leads:
            lines.append(
                "• подтверждённых нарушений первого ответа: "
                f"{len(lead_items)}"
            )
            if lead_items:
                lines.append(
                    "Лиды для проверки "
                    f"(показано {min(len(lead_items), cards_per_manager)} "
                    f"из {len(lead_items)}):"
                )

            for item in lead_items[:cards_per_manager]:
                elapsed_minutes = _minutes(
                    item.elapsed_business_seconds
                )
                threshold_minutes = _minutes(
                    item.threshold_business_seconds
                )
                excess_minutes = _minutes(
                    max(
                        0,
                        item.elapsed_business_seconds
                        - item.threshold_business_seconds,
                    )
                )
                created = item.created_at.astimezone(zone).strftime(
                    "%d.%m %H:%M"
                )
                response = (
                    item.response_at.astimezone(zone).strftime(
                        "%d.%m %H:%M"
                    )
                    if item.response_at is not None
                    else "не подтверждён"
                )
                lead_url = _entity_url(
                    settings,
                    "lead",
                    item.lead_id,
                )
                lead_label = f"Лид #{item.lead_id}"
                if lead_url.startswith("https://"):
                    lead_label = (
                        f"[{lead_label}]({lead_url})"
                    )
                lines.extend(
                    [
                        (
                            f"• {lead_label} · первый подтверждённый "
                            f"ответ {elapsed_minutes} бизнес-мин. "
                            f"(норматив {threshold_minutes}, превышение "
                            f"{excess_minutes})"
                        ),
                        f"  создан: {created} · ответ: {response}",
                        (
                            "  причина: подтверждённый первый ответ "
                            "позже SLA"
                        ),
                    ]
                )
                if not lead_url.startswith("https://"):
                    lines.append(
                        f"  {lead_url}"
                    )

        if include_deals:
            lines.append(
                "• сделок, требующих внимания по Stage SLA: "
                f"{len(deal_items)}"
            )
            if deal_items:
                lines.append(
                    "Сделки для проверки "
                    f"(показано {min(len(deal_items), cards_per_manager)} "
                    f"из {len(deal_items)}):"
                )

            for item in deal_items[:cards_per_manager]:
                deadline = (
                    item.deadline_at.astimezone(zone).strftime(
                        "%d.%m %H:%M"
                    )
                    if item.deadline_at is not None
                    else "не определён"
                )
                deal_url = _entity_url(
                    settings,
                    "deal",
                    item.deal_id,
                )
                deal_label = f"Сделка #{item.deal_id}"
                if deal_url.startswith("https://"):
                    deal_label = (
                        f"[{deal_label}]({deal_url})"
                    )
                lines.extend(
                    [
                        (
                            f"• {deal_label} · "
                            f"{item.stage_label} · срок {deadline}"
                        ),
                        (
                            "  причина: срок Stage SLA прошёл; "
                            "коммуникации проверяются отдельно"
                        ),
                    ]
                )
                if not deal_url.startswith("https://"):
                    lines.append(
                        f"  {deal_url}"
                    )

        if not lead_items and not deal_items:
            lines.append(
                "• точных проблемных карточек в выбранном срезе нет"
            )

    if hidden_managers:
        lines.extend(
            [
                "",
                (
                    f"Ещё менеджеров с карточками: {hidden_managers}. "
                    "Запросите следующую группу или конкретного менеджера."
                ),
            ]
        )

    lines.extend(
        [
            "",
            "Клиентские тексты и телефоны не передаются ИИ.",
            "BITRIX WRITES = NONE",
        ]
    )
    return "\n".join(lines)


def format_b2c_today_focus_for_ai(
    dashboard: B2CMvpDashboard,
    settings: Settings,
    *,
    limit: int = 5,
) -> str:
    """Format the global B2C Stage SLA priority list without legacy noise."""

    if limit < 1 or limit > 10:
        raise ValueError("focus_limit_out_of_range")

    zone = ZoneInfo(settings.rop_timezone)
    attention_deals = sorted(
        (
            item
            for item in dashboard.stage_sla.deals
            if item.requires_attention
        ),
        key=lambda item: (
            item.deadline_at is None,
            item.deadline_at,
            item.deal_id,
        ),
    )
    selected = attention_deals[:limit]

    lines = [
        "ИИ-РОП · B2C · что проверить сегодня",
        (
            "Срез: "
            + dashboard.cutoff_at.astimezone(zone).strftime(
                "%d.%m.%Y %H:%M %Z"
            )
        ),
        (
            "Это текущий B2C backlog для вмешательства, "
            "а не сделки, созданные сегодня."
        ),
        "",
        (
            f"Требуют внимания по Stage SLA: "
            f"{dashboard.stage_sla.attention}"
        ),
        f"Показано первых: {len(selected)}",
    ]

    if not selected:
        lines.extend(
            [
                "",
                "Подтверждённых B2C-сделок для срочного разбора нет.",
            ]
        )

    for index, item in enumerate(selected, start=1):
        deal_url = _entity_url(
            settings,
            "deal",
            item.deal_id,
        )
        deal_label = f"Сделка #{item.deal_id}"
        if deal_url.startswith("https://"):
            deal_label = f"[{deal_label}]({deal_url})"

        deadline = (
            item.deadline_at.astimezone(zone).strftime(
                "%d.%m %H:%M"
            )
            if item.deadline_at is not None
            else "не определён"
        )
        lines.extend(
            [
                "",
                f"{index}. {deal_label} · {item.stage_label}",
                f"   менеджер: {item.manager_name}",
                f"   контрольный срок: {deadline}",
                "   почему: срок Stage SLA прошёл",
            ]
        )
        if not deal_url.startswith("https://"):
            lines.append(
                f"   {deal_url}"
            )

    hidden = max(
        0,
        len(attention_deals) - len(selected),
    )
    if hidden:
        lines.extend(
            [
                "",
                f"Осталось в очереди разбора: {hidden}.",
            ]
        )

    lines.extend(
        [
            "",
            (
                "Stage SLA означает превышение контрольного срока стадии; "
                "это не доказательство отсутствия коммуникации."
            ),
            "BITRIX WRITES = NONE",
        ]
    )
    return "\n".join(lines)


def build_and_format_b2c_problem_cards(
    settings: Settings,
    *,
    scope: ProblemCardScope = "all",
    manager_id: str | None = None,
    max_managers: int = 5,
    cards_per_manager: int = 3,
) -> str:
    dashboard = build_b2c_mvp_dashboard(settings)
    return format_b2c_problem_cards_for_ai(
        dashboard,
        settings,
        scope=scope,
        manager_id=manager_id,
        max_managers=max_managers,
        cards_per_manager=cards_per_manager,
    )


def build_and_format_b2c_today_focus(
    settings: Settings,
    *,
    limit: int = 5,
) -> str:
    dashboard = build_b2c_mvp_dashboard(settings)
    return format_b2c_today_focus_for_ai(
        dashboard,
        settings,
        limit=limit,
    )
