from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.services.rop_analytics import build_rop_snapshot
from app.services.rop_catalog import category_label, stage_label
from app.services.rop_deep_analytics import build_manager_report
from app.services.rop_directory import RopDirectory, employee_label, load_rop_directory
from app.services.rop_mvp3 import build_focus_report, build_stage_sla_report


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def _closed_conversion(won: int, lost: int) -> str:
    closed = won + lost
    if closed <= 0:
        return "нет закрытых сделок"
    return f"{100 * won / closed:.1f}% (n={closed})"


def _employee(directory: RopDirectory, user_id: str) -> str:
    return employee_label(directory, user_id, include_id=False)


async def build_rop_daily(settings: Settings) -> str:
    snapshot = await build_rop_snapshot(
        settings.database_path,
        attention_days=settings.rop_attention_days,
        critical_days=settings.rop_critical_days,
        risk_limit=settings.rop_risk_limit,
        timezone_name=settings.rop_timezone,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )
    sla = await build_stage_sla_report(
        settings.database_path,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )
    focus = await build_focus_report(
        settings.database_path,
        limit=settings.rop_focus_limit,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )
    managers = await build_manager_report(
        settings.database_path,
        timezone_name=settings.rop_timezone,
        attention_days=settings.rop_attention_days,
        critical_days=settings.rop_critical_days,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )
    directory = await load_rop_directory(settings.database_path)

    month = snapshot.period("month")
    lines = ["ИИ-РОП · Daily Brief", "Локальная read-only сводка для руководителя."]

    if month is not None:
        lines.extend(
            [
                "\nТекущий месяц:",
                f"• новые лиды: {month.new_leads}",
                f"• новые сделки: {month.new_deals}",
                f"• WON / LOST: {month.won_deals} / {month.lost_deals}",
                "• конверсия закрытых: "
                f"{_closed_conversion(month.won_deals, month.lost_deals)}",
            ]
        )
        if month.won_revenue_by_currency:
            won_amounts = ", ".join(
                f"{currency} {_money(amount)}"
                for currency, amount in month.won_revenue_by_currency
            )
            lines.append(f"• сумма успешных WON: {won_amounts}")

    lines.append("\nГде горит по SLA:")
    if not sla:
        lines.append("• критических измеряемых стадий не найдено")
    else:
        for item in sla[:4]:
            share = 100 * item.critical_count / item.active_count if item.active_count else 0
            lines.append(
                f"• {category_label(item.rule.category_id)} · "
                f"{stage_label(item.rule.stage_id)}: критично {item.critical_count} из "
                f"{item.active_count} ({share:.1f}%)"
            )

    money = [item for item in focus.deals if item.business_bucket == "money"]
    hygiene = [item for item in focus.deals if item.business_bucket == "hygiene"]

    lines.append("\nСделки для вмешательства сегодня:")
    if not money:
        lines.append("• нет денежных SLA-кандидатов в текущем focus-list")
    else:
        for item in money[:5]:
            severity = "КРИТИЧНО" if item.severity == "critical" else "ВНИМАНИЕ"
            lines.append(
                f"• [{severity}] #{item.deal_id} | {category_label(item.category_id)} · "
                f"{stage_label(item.stage_id)} | {item.age_days} дн. | "
                f"{_money(item.opportunity)} {item.currency} | "
                f"{_employee(directory, item.assigned_by_id)}"
            )

    lines.append("\nКого разбирать сегодня:")
    manager_candidates = [item for item in managers.managers if item.critical_count > 0]
    if not manager_candidates:
        lines.append("• менеджеров с критическими активными карточками не найдено")
    else:
        for item in manager_candidates[:5]:
            closed = item.month_won + item.month_lost
            sample = (
                f"закрытых {closed}, конверсия "
                f"{100 * item.month_won / closed:.1f}%"
                if closed >= settings.rop_manager_min_closed_sample
                else f"закрытых {closed}, выборка мала"
            )
            lines.append(
                f"• {_employee(directory, item.assigned_by_id)} | активных "
                f"{item.active_count} | 5+ дней {item.critical_count} | "
                f"WON/LOST {item.month_won}/{item.month_lost} | {sample}"
            )

    lines.extend(
        [
            "\nCRM hygiene:",
            f"• SLA-кандидатов с нулевой/технической суммой ≤1: {focus.hygiene_candidates}",
        ]
    )
    for item in hygiene[:3]:
        lines.append(
            f"• #{item.deal_id} | {category_label(item.category_id)} · "
            f"{stage_label(item.stage_id)} | {item.age_days} дн. | "
            f"{_employee(directory, item.assigned_by_id)}"
        )

    lines.append("\nПриоритет действий:")
    if money:
        top = money[0]
        lines.append(
            f"1. Проверить сделку #{top.deal_id}: {top.rule_label}, "
            f"{top.age_days} дн. на текущей стадии."
        )
    if manager_candidates:
        top_manager = manager_candidates[0]
        lines.append(
            f"2. Разобрать портфель {_employee(directory, top_manager.assigned_by_id)}: "
            f"критических активных карточек {top_manager.critical_count}."
        )
    if sla:
        top_sla = sla[0]
        lines.append(
            f"3. Снять хвост стадии {category_label(top_sla.rule.category_id)} · "
            f"{stage_label(top_sla.rule.stage_id)}: критично {top_sla.critical_count}."
        )

    lines.append(
        "\nФИО и отделы берутся из локального справочника Bitrix24. Сырые карточки CRM "
        "и справочник сотрудников в LLM для этого отчёта не передаются."
    )
    return "\n".join(lines)
