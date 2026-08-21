from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.config import Settings
from app.services.rop_activity_risk import (
    ActivityAwareDealRisk,
    build_activity_aware_risk,
    format_activity_aware_risk_compact,
)
from app.services.rop_analytics import build_rop_snapshot
from app.services.rop_b2c_first_response_truth import (
    build_b2c_first_response_truth,
)
from app.services.rop_catalog import category_label, stage_label
from app.services.rop_deal import build_deal_drilldown
from app.services.rop_deal_evidence import build_deal_stage_evidence
from app.services.rop_deal_vitality import (
    DealVitality,
    build_deal_vitality,
    format_deal_vitality_compact,
)
from app.services.rop_deep_analytics import build_manager_report
from app.services.rop_directory import RopDirectory, employee_label, load_rop_directory
from app.services.rop_mvp3 import FocusDeal, build_focus_report, build_stage_sla_report


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def _closed_conversion(won: int, lost: int) -> str:
    closed = won + lost
    if closed <= 0:
        return "нет закрытых сделок"
    return f"{100 * won / closed:.1f}% (n={closed})"


def _employee(directory: RopDirectory, user_id: str) -> str:
    return employee_label(directory, user_id, include_id=False)


def _responsible_label(directory: RopDirectory, user_id: str) -> str:
    if user_id in directory.users:
        return _employee(directory, user_id)
    return f"actor ID {user_id} · исключён из manager attribution"


def _partition_focus_attribution(
    deals: tuple[FocusDeal, ...],
    directory: RopDirectory,
) -> tuple[
    defaultdict[str, dict[str, int]],
    defaultdict[str, dict[str, int]],
]:
    def bucket() -> dict[str, int]:
        return {"critical": 0, "attention": 0, "critical_money": 0}

    human: defaultdict[str, dict[str, int]] = defaultdict(bucket)
    excluded: defaultdict[str, dict[str, int]] = defaultdict(bucket)
    for item in deals:
        target = human if item.assigned_by_id in directory.users else excluded
        counters = target[item.assigned_by_id]
        if item.severity == "critical":
            counters["critical"] += 1
            if item.business_bucket == "money":
                counters["critical_money"] += 1
        else:
            counters["attention"] += 1
    return human, excluded


async def _build_top_deal_context(
    settings: Settings,
    deal_ids: list[str],
) -> dict[str, tuple[ActivityAwareDealRisk, DealVitality]]:
    result: dict[str, tuple[ActivityAwareDealRisk, DealVitality]] = {}
    for deal_id in deal_ids:
        report = await build_deal_drilldown(
            settings,
            deal_id,
            include_timeline_comments=False,
        )
        if report is None:
            continue
        evidence = await build_deal_stage_evidence(settings, report)
        risk = build_activity_aware_risk(report, evidence)
        vitality = build_deal_vitality(report, risk)
        result[deal_id] = (risk, vitality)
    return result


async def build_rop_daily(settings: Settings) -> str:
    first_response = build_b2c_first_response_truth(
        settings.database_path
    )

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
    lines = ["ИИ-РОП · Daily Brief", "Локальная сводка для руководителя · без записи в Bitrix."]

    if month is not None:
        lines.extend(
            [
                "\nТекущий месяц:",
                f"• все лиды CRM: {first_response.all_leads_created}",
                f"• подтверждённые B2C-лиды: {first_response.b2c_proven}",
                f"• B2C не подтверждено: {first_response.unresolved}",
                f"• исключено / вне B2C: {first_response.excluded_or_out_of_scope}",
                f"• новые сделки: {month.new_deals}",
                f"• WON / LOST: {month.won_deals} / {month.lost_deals}",
                f"• конверсия закрытых: {_closed_conversion(month.won_deals, month.lost_deals)}",
            ]
        )
        if month.won_revenue_by_currency:
            won_amounts = ", ".join(
                f"{currency} {_money(amount)}" for currency, amount in month.won_revenue_by_currency
            )
            lines.append(f"• сумма успешных WON: {won_amounts}")

    attributed_first_response_breaches = max(
        0,
        first_response.breach
        - first_response.unattributed_breaches,
    )

    lines.extend(
        [
            "\nFirst Response SLA · 15 бизнес-минут:",
            (
                f"• измерено: {first_response.measured} из "
                f"{first_response.b2c_proven} "
                f"({first_response.measured_share_percent:.1f}%)"
            ),
            f"• в SLA: {first_response.ok}",
            f"• нарушение: {first_response.breach}",
            f"• ещё открыто: {first_response.open}",
            (
                "• blocked / недостаточно evidence: "
                f"{first_response.blocked}"
            ),
            (
                "• соблюдение среди измеренных закрытых: "
                f"{first_response.ok_share_closed_percent:.1f}% "
                f"(n={first_response.closed_measured})"
            ),
            (
                "• нарушений с безопасной атрибуцией менеджеру: "
                f"{attributed_first_response_breaches}"
            ),
            (
                "• нарушений без безопасной атрибуции: "
                f"{first_response.unattributed_breaches}"
            ),
        ]
    )

    if first_response.blocked > 0:
        lines.append(
            "• blocked НЕ считается нарушением: "
            "для этих кейсов недостаточно доказательств."
        )

    lines.append(
        "\nAging-risk по текущим стадиям (НЕ Stage SLA):"
    )
    if not sla:
        lines.append("• aging-кандидатов по legacy-правилам не найдено")
    else:
        for item in sla[:4]:
            share = 100 * item.critical_count / item.active_count if item.active_count else 0
            lines.append(
                f"• {category_label(item.rule.category_id)} · "
                f"{stage_label(item.rule.stage_id)}: aging-критично {item.critical_count} из "
                f"{item.active_count} ({share:.1f}%)"
            )

    money = [item for item in focus.deals if item.business_bucket == "money"]
    hygiene = [item for item in focus.deals if item.business_bucket == "hygiene"]
    top_context = await _build_top_deal_context(
        settings,
        [item.deal_id for item in money[:3]],
    )

    lines.append("\nСделки для проверки по aging-risk:")
    if not money:
        lines.append("• нет денежных aging-кандидатов в текущем focus-list")
    else:
        for item in money[:5]:
            severity = "AGING-КРИТИЧНО" if item.severity == "critical" else "AGING-ВНИМАНИЕ"
            context = top_context.get(item.deal_id)
            vitality_suffix = ""
            if context is not None:
                vitality_suffix = " | " + format_deal_vitality_compact(context[1])
            lines.append(
                f"• [{severity}] #{item.deal_id} | {category_label(item.category_id)} · "
                f"{stage_label(item.stage_id)} | {item.age_days} дн. | "
                f"{_money(item.opportunity)} {item.currency} | "
                f"{_responsible_label(directory, item.assigned_by_id)}{vitality_suffix}"
            )

        lines.append("\nActivity-aware + vitality сигналы по top-3 сделкам:")
        if not top_context:
            lines.append("• детальные сигналы не удалось построить")
        else:
            for item in money[:3]:
                context = top_context.get(item.deal_id)
                if context is None:
                    continue
                risk, vitality = context
                lines.append(f"• #{item.deal_id} | {format_activity_aware_risk_compact(risk)}")
                lines.append(f"  {format_deal_vitality_compact(vitality)}")
        lines.append(
            "• дни с последней коммуникации являются фактом, а не отдельным SLA: "
            "норматив допустимой паузы между контактами пока не задан."
        )
        lines.append(
            "• неподтверждённый pipeline означает: сумма есть в CRM, но актуальность "
            "сделки надо подтвердить до управленческой трактовки как рабочего pipeline."
        )

    manager_by_id = {item.assigned_by_id: item for item in managers.managers}
    sla_by_manager, excluded_sla_by_actor = _partition_focus_attribution(
        focus.deals,
        directory,
    )

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

    lines.append("\nКого разбирать по aging-risk (НЕ Stage SLA):")
    if not manager_candidates:
        lines.append("• менеджеров с SLA-критичными карточками не найдено")
    else:
        for manager_id, counters, manager_stat in manager_candidates[:5]:
            if manager_stat is None:
                lines.append(
                    f"• {_employee(directory, manager_id)} | aging-критично "
                    f"{counters['critical']} | с суммой >1 {counters['critical_money']} | "
                    f"aging-внимание (без критичных) {counters['attention']}"
                )
                continue

            closed = manager_stat.month_won + manager_stat.month_lost
            sample = (
                f"закрытых {closed}, конверсия {100 * manager_stat.month_won / closed:.1f}%"
                if closed >= settings.rop_manager_min_closed_sample
                else f"закрытых {closed}, выборка мала"
            )
            lines.append(
                f"• {_employee(directory, manager_id)} | aging-критично "
                f"{counters['critical']} | с суммой >1 {counters['critical_money']} | "
                f"aging-внимание (без критичных) {counters['attention']} | "
                f"WON/LOST {manager_stat.month_won}/{manager_stat.month_lost} | {sample}"
            )

    if excluded_sla_by_actor:
        lines.append("\nИсключённая aging-атрибуция · НЕ менеджеры:")
        for actor_id, counters in sorted(
            excluded_sla_by_actor.items(),
            key=lambda item: (
                item[1]["critical"],
                item[1]["critical_money"],
                item[1]["attention"],
            ),
            reverse=True,
        )[:5]:
            lines.append(
                f"• actor ID {actor_id} | aging-критично {counters['critical']} | "
                f"с суммой >1 {counters['critical_money']} | "
                f"aging-внимание {counters['attention']}"
            )

    lines.extend(
        [
            "\nCRM hygiene:",
            f"• aging-кандидатов с нулевой/технической суммой ≤1: {focus.hygiene_candidates}",
        ]
    )
    for item in hygiene[:3]:
        lines.append(
            f"• #{item.deal_id} | {category_label(item.category_id)} · "
            f"{stage_label(item.stage_id)} | {item.age_days} дн. | "
            f"{_responsible_label(directory, item.assigned_by_id)}"
        )

    lines.append("\nПриоритет действий:")
    if money:
        top = money[0]
        context = top_context.get(top.deal_id)
        vitality = context[1] if context is not None else None
        if vitality is not None and vitality.pipeline_confidence == "unconfirmed":
            lines.append(
                f"1. Сначала подтвердить актуальность сделки #{top.deal_id}: "
                f"{_money(top.opportunity)} {top.currency} пока считается "
                "неподтверждённым pipeline для управленческого решения."
            )
        else:
            lines.append(
                f"1. Проверить сделку #{top.deal_id}: {top.rule_label}, "
                f"{top.age_days} дн. на текущей стадии."
            )
    if manager_candidates:
        manager_id, counters, _manager_stat = manager_candidates[0]
        lines.append(
            f"2. Разобрать портфель {_employee(directory, manager_id)}: "
            f"aging-критично {counters['critical']}, из них с суммой >1 "
            f"{counters['critical_money']}."
        )
    if sla:
        top_sla = sla[0]
        lines.append(
            f"3. Снять хвост стадии {category_label(top_sla.rule.category_id)} · "
            f"{stage_label(top_sla.rule.stage_id)}: aging-критично {top_sla.critical_count}."
        )

    lines.append(
        "\nHuman manager attribution в Daily Brief ограничена DIRECTORY_USER. "
        "Special/unresolved/non-directory actor остаётся в deal/team фактах, но "
        "не попадает в блок 'Кого разбирать' и показывается отдельно как excluded attribution."
    )
    lines.append(
        "Manager ranking в этом блоке использует legacy aging-risk, а не Stage SLA. "
        "aging-внимание означает жёлтую зону и не включает уже aging-критичные карточки. "
        "Общий aging 3+/5+ из /rop_managers не считается Stage SLA конкретной стадии."
    )
    lines.append(
        "Deal vitality не является вероятностью продажи и не закрывает сделки автоматически. "
        "Он отделяет рабочие сигналы от карточек, актуальность которых надо подтвердить."
    )
    lines.append(
        "ФИО и отделы берутся из локального справочника Bitrix24. Сырые карточки CRM "
        "и справочник сотрудников в LLM для этого отчёта не передаются."
    )
    return "\n".join(lines)
