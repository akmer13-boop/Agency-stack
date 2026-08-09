from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.config import Settings
from app.services.rop_activity_risk import (
    build_activity_aware_risk,
    format_activity_aware_risk_compact,
)
from app.services.rop_analytics import build_rop_snapshot
from app.services.rop_catalog import category_label, stage_label
from app.services.rop_deal import build_deal_drilldown
from app.services.rop_deal_evidence import build_deal_stage_evidence
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
    # Daily manager prioritization must see the full SLA candidate set, not only the
    # user-facing focus limit. The report itself still displays only a short top list.
    focus = await build_focus_report(
        settings.database_path,
        limit=10_000,
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

        lines.append("\nActivity-aware сигналы по top-3 сделкам:")
        risk_cards = 0
        for item in money[:3]:
            report = await build_deal_drilldown(
                settings,
                item.deal_id,
                include_timeline_comments=False,
            )
            if report is None:
                continue
            evidence = await build_deal_stage_evidence(settings, report)
            risk = build_activity_aware_risk(report, evidence)
            lines.append(
                f"• #{item.deal_id} | {format_activity_aware_risk_compact(risk)}"
            )
            risk_cards += 1
        if risk_cards == 0:
            lines.append("• детальные activity-aware сигналы не удалось построить")
        lines.append(
            "• дни с последней коммуникации здесь являются фактом, а не отдельным SLA: "
            "норматив допустимой паузы между контактами пока не задан."
        )

    manager_by_id = {item.assigned_by_id: item for item in managers.managers}
    sla_by_manager: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"critical": 0, "attention": 0, "critical_money": 0}
    )
    for item in focus.deals:
        counters = sla_by_manager[item.assigned_by_id]
        if item.severity == "critical":
            counters["critical"] += 1
            if item.business_bucket == "money":
                counters["critical_money"] += 1
        else:
            counters["attention"] += 1

    manager_candidates = [
        (manager_id, counters, manager_by_id.get(manager_id))
        for manager_id, counters in sla_by_manager.items()
        if counters["critical"] > 0
    ]
    manager_candidates.sort(
        key=lambda item: (
            item[1]["critical"],
            item[1]["critical_money"],
            item[1]["attention"],
            item[2].month_lost if item[2] is not None else 0,
            item[2].active_count if item[2] is not None else 0,
        ),
        reverse=True,
    )

    lines.append("\nКого разбирать сегодня по stage-specific SLA:")
    if not manager_candidates:
        lines.append("• менеджеров с SLA-критичными карточками не найдено")
    else:
        for manager_id, counters, manager_stat in manager_candidates[:5]:
            if manager_stat is None:
                lines.append(
                    f"• {_employee(directory, manager_id)} | SLA-критично "
                    f"{counters['critical']} | с суммой >1 {counters['critical_money']} | "
                    f"SLA-внимание (без критичных) {counters['attention']}"
                )
                continue

            closed = manager_stat.month_won + manager_stat.month_lost
            sample = (
                f"закрытых {closed}, конверсия "
                f"{100 * manager_stat.month_won / closed:.1f}%"
                if closed >= settings.rop_manager_min_closed_sample
                else f"закрытых {closed}, выборка мала"
            )
            lines.append(
                f"• {_employee(directory, manager_id)} | SLA-критично "
                f"{counters['critical']} | с суммой >1 {counters['critical_money']} | "
                f"SLA-внимание (без критичных) {counters['attention']} | "
                f"WON/LOST {manager_stat.month_won}/{manager_stat.month_lost} | {sample}"
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
        manager_id, counters, _manager_stat = manager_candidates[0]
        lines.append(
            f"2. Разобрать портфель {_employee(directory, manager_id)}: "
            f"SLA-критично {counters['critical']}, из них с суммой >1 "
            f"{counters['critical_money']}."
        )
    if sla:
        top_sla = sla[0]
        lines.append(
            f"3. Снять хвост стадии {category_label(top_sla.rule.category_id)} · "
            f"{stage_label(top_sla.rule.stage_id)}: критично {top_sla.critical_count}."
        )

    lines.append(
        "\nManager ranking в этом Daily Brief использует только stage-specific SLA. "
        "SLA-внимание означает жёлтую зону и не включает уже критичные карточки. "
        "Общий aging 3+/5+ из /rop_managers не считается SLA конкретной стадии."
    )
    lines.append(
        "ФИО и отделы берутся из локального справочника Bitrix24. Сырые карточки CRM "
        "и справочник сотрудников в LLM для этого отчёта не передаются."
    )
    return "\n".join(lines)
