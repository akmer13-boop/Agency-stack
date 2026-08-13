from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.semantic.activity_classifier import (
    ActivityClassification,
    classify_activity,
)
from app.semantic.models import SemanticDeal, SemanticLead
from app.semantic.repository import SemanticRepository
from app.services.rop_directory import load_rop_directory

_LEAD_OWNER_TYPE_ID = "1"
_DEAL_OWNER_TYPE_ID = "2"
_SALES_OWNER_TYPE_IDS = frozenset({_LEAD_OWNER_TYPE_ID, _DEAL_OWNER_TYPE_ID})


@dataclass(frozen=True, slots=True)
class BusinessRuleGap:
    key: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class StageCountFact:
    stage_id: str
    semantic: str
    count: int


@dataclass(frozen=True, slots=True)
class ManagerFact:
    manager_id: str
    display_name: str
    employee_active: bool | None
    department_names: tuple[str, ...]
    current_active_deals: int
    current_won_deals: int
    current_lost_deals: int
    current_active_leads: int
    current_success_leads: int
    current_failed_leads: int
    won_crm_opportunity_by_currency: tuple[tuple[str, Decimal], ...]
    sales_activities_total: int
    sales_activities_completed: int
    sales_activities_open: int
    confirmed_communications: int
    manager_evidence_activities: int
    human_actions: int
    system_activities: int
    unknown_activities: int


@dataclass(frozen=True, slots=True)
class ManagementFactSnapshot:
    generated_at: datetime
    active_deals: int
    won_deals: int
    lost_deals: int
    active_leads: int
    successful_leads: int
    failed_leads: int
    sales_activities_total: int
    deal_stage_history_events: int
    lead_stage_history_events: int
    deal_stage_counts: tuple[StageCountFact, ...]
    lead_status_counts: tuple[StageCountFact, ...]
    activity_classification_counts: tuple[tuple[str, int], ...]
    managers: tuple[ManagerFact, ...]
    pending_business_rules: tuple[BusinessRuleGap, ...]


_PENDING_BUSINESS_RULES = (
    BusinessRuleGap(
        "first_response_sla",
        "pending_business_approval",
        (
            "timer start, response event, work clock, weekends/holidays, "
            "reassignment and threshold are not fully approved"
        ),
    ),
    BusinessRuleGap(
        "stale_deal",
        "pending_business_approval",
        "reset activity and normative inactivity window are not approved",
    ),
    BusinessRuleGap(
        "proposal_stale",
        "pending_business_approval",
        "proposal-sent event and allowed follow-up window are not approved",
    ),
    BusinessRuleGap(
        "business_conversion",
        "pending_business_approval",
        ("source cohort, target event, pipelines and attribution rules are not approved"),
    ),
    BusinessRuleGap(
        "manager_rating",
        "pending_business_approval",
        ("weights, normalization, minimum sample and exclusions are not approved"),
    ),
    BusinessRuleGap(
        "sales_plan_fact",
        "pending_business_approval",
        ("plan source, period, ownership level and financial measure are not approved"),
    ),
    BusinessRuleGap(
        "management_escalation",
        "pending_business_approval",
        "mandatory intervention conditions are not approved",
    ),
)


def _semantic(value: str | None) -> str:
    return (value or "P").strip().upper() or "P"


def _currency(value: str | None) -> str:
    return (value or "N/A").strip().upper() or "N/A"


def _deal_state(deal: SemanticDeal) -> str:
    semantic = _semantic(deal.stage_semantic)
    if semantic == "S":
        return "won"
    if semantic == "F":
        return "lost"
    return "active"


def _lead_state(lead: SemanticLead) -> str:
    semantic = _semantic(lead.status_semantic)
    if semantic == "S":
        return "success"
    if semantic == "F":
        return "failed"
    return "active"


def _stage_counts(
    values: list[tuple[str | None, str | None]],
) -> tuple[StageCountFact, ...]:
    counter: Counter[tuple[str, str]] = Counter(
        (
            (stage_id or "NOT_SET").strip() or "NOT_SET",
            (semantic or "NOT_SET").strip().upper() or "NOT_SET",
        )
        for stage_id, semantic in values
    )
    return tuple(
        StageCountFact(
            stage_id=stage_id,
            semantic=semantic,
            count=count,
        )
        for (stage_id, semantic), count in sorted(
            counter.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    )


def _count(
    counters: defaultdict[str, Counter[str]],
    manager_id: str,
    key: str,
) -> int:
    return int(counters[manager_id][key])


def _manager_sort_key(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, value)


async def build_management_facts(
    database_path: str,
    *,
    now: datetime | None = None,
) -> ManagementFactSnapshot:
    """Build policy-free deterministic management facts from the active CRM view."""

    reference = (now or datetime.now(UTC)).astimezone(UTC)
    repository = SemanticRepository(database_path)
    deals = await repository.deals()
    leads = await repository.leads()
    activities = await repository.activities()
    deal_history = await repository.deal_stage_history()
    lead_history = await repository.lead_stage_history()
    directory = await load_rop_directory(database_path)

    manager_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    manager_amounts: defaultdict[
        str,
        defaultdict[str, Decimal],
    ] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    manager_ids: set[str] = set()

    team_deals = Counter()
    for deal in deals:
        state = _deal_state(deal)
        team_deals[state] += 1
        if deal.assigned_user_id is None:
            continue
        manager_id = deal.assigned_user_id
        manager_ids.add(manager_id)
        manager_counts[manager_id][f"deal_{state}"] += 1
        if state == "won":
            manager_amounts[manager_id][_currency(deal.currency)] += deal.amount

    team_leads = Counter()
    for lead in leads:
        state = _lead_state(lead)
        team_leads[state] += 1
        if lead.assigned_user_id is None:
            continue
        manager_id = lead.assigned_user_id
        manager_ids.add(manager_id)
        manager_counts[manager_id][f"lead_{state}"] += 1

    classification_counts: Counter[str] = Counter()
    sales_activities_total = 0
    for activity in activities:
        if activity.owner_entity_type not in _SALES_OWNER_TYPE_IDS:
            continue
        sales_activities_total += 1
        evidence = classify_activity(activity)
        classification_counts[evidence.classification.value] += 1

        if activity.responsible_user_id is None:
            continue
        manager_id = activity.responsible_user_id
        manager_ids.add(manager_id)
        manager_counts[manager_id]["activity_total"] += 1
        activity_state = "activity_completed" if activity.completed else "activity_open"
        manager_counts[manager_id][activity_state] += 1

        if evidence.classification is ActivityClassification.CONFIRMED_COMMUNICATION:
            manager_counts[manager_id]["confirmed_communication"] += 1
        elif evidence.classification is ActivityClassification.HUMAN_ACTION:
            manager_counts[manager_id]["human_action"] += 1
        elif evidence.classification is ActivityClassification.SYSTEM_ACTIVITY:
            manager_counts[manager_id]["system_activity"] += 1
        else:
            manager_counts[manager_id]["unknown_activity"] += 1
        if evidence.is_manager_evidence:
            manager_counts[manager_id]["manager_evidence"] += 1

    managers: list[ManagerFact] = []
    for manager_id in sorted(manager_ids, key=_manager_sort_key):
        identity = directory.users.get(manager_id)
        managers.append(
            ManagerFact(
                manager_id=manager_id,
                display_name=(
                    identity.display_name if identity is not None else f"ID {manager_id}"
                ),
                employee_active=identity.active if identity is not None else None,
                department_names=(identity.department_names if identity is not None else ()),
                current_active_deals=_count(manager_counts, manager_id, "deal_active"),
                current_won_deals=_count(manager_counts, manager_id, "deal_won"),
                current_lost_deals=_count(manager_counts, manager_id, "deal_lost"),
                current_active_leads=_count(manager_counts, manager_id, "lead_active"),
                current_success_leads=_count(manager_counts, manager_id, "lead_success"),
                current_failed_leads=_count(manager_counts, manager_id, "lead_failed"),
                won_crm_opportunity_by_currency=tuple(sorted(manager_amounts[manager_id].items())),
                sales_activities_total=_count(manager_counts, manager_id, "activity_total"),
                sales_activities_completed=_count(manager_counts, manager_id, "activity_completed"),
                sales_activities_open=_count(manager_counts, manager_id, "activity_open"),
                confirmed_communications=_count(
                    manager_counts, manager_id, "confirmed_communication"
                ),
                manager_evidence_activities=_count(manager_counts, manager_id, "manager_evidence"),
                human_actions=_count(manager_counts, manager_id, "human_action"),
                system_activities=_count(manager_counts, manager_id, "system_activity"),
                unknown_activities=_count(manager_counts, manager_id, "unknown_activity"),
            )
        )

    return ManagementFactSnapshot(
        generated_at=reference,
        active_deals=int(team_deals["active"]),
        won_deals=int(team_deals["won"]),
        lost_deals=int(team_deals["lost"]),
        active_leads=int(team_leads["active"]),
        successful_leads=int(team_leads["success"]),
        failed_leads=int(team_leads["failed"]),
        sales_activities_total=sales_activities_total,
        deal_stage_history_events=len(deal_history),
        lead_stage_history_events=len(lead_history),
        deal_stage_counts=_stage_counts([(item.stage_id, item.stage_semantic) for item in deals]),
        lead_status_counts=_stage_counts(
            [(item.status_id, item.status_semantic) for item in leads]
        ),
        activity_classification_counts=tuple(
            sorted(
                classification_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        managers=tuple(managers),
        pending_business_rules=_PENDING_BUSINESS_RULES,
    )


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def _manager_label(item: ManagerFact) -> str:
    department = " / ".join(item.department_names)
    name = f"{item.display_name} · {department}" if department else item.display_name
    return f"{name} (ID {item.manager_id})"


def format_management_facts_for_ai(
    snapshot: ManagementFactSnapshot,
    *,
    manager_id: str | None = None,
    manager_limit: int = 50,
) -> str:
    selected = list(snapshot.managers)
    if manager_id is not None:
        selected = [item for item in selected if item.manager_id == str(manager_id)]

    lines = [
        "ИИ-РОП · Deterministic Management Facts",
        f"Generated UTC: {snapshot.generated_at.isoformat()}",
        "",
        "CURRENT CRM FACTS:",
        (
            f"• deals: active {snapshot.active_deals}, "
            f"WON {snapshot.won_deals}, LOST {snapshot.lost_deals}"
        ),
        (
            f"• leads: active {snapshot.active_leads}, "
            f"successful {snapshot.successful_leads}, failed {snapshot.failed_leads}"
        ),
        (f"• sales CRM activities linked to lead/deal: {snapshot.sales_activities_total}"),
        f"• deal stage-history events stored: {snapshot.deal_stage_history_events}",
        f"• lead stage-history events stored: {snapshot.lead_stage_history_events}",
    ]
    if snapshot.activity_classification_counts:
        classes = ", ".join(
            f"{name} {count}" for name, count in snapshot.activity_classification_counts
        )
        lines.append(f"• activity evidence classes: {classes}")

    lines.extend(["", "RESPONSIBLE FACTS — NOT A RATING / NOT A RANKING:"])
    if not selected:
        lines.append(
            "• no responsible users observed in active sales facts"
            if manager_id is None
            else f"• no active sales facts found for responsible ID {manager_id}"
        )

    for item in selected[:manager_limit]:
        lines.append(
            f"• {_manager_label(item)} | deals active/WON/LOST "
            f"{item.current_active_deals}/{item.current_won_deals}/"
            f"{item.current_lost_deals} | leads active/success/failed "
            f"{item.current_active_leads}/{item.current_success_leads}/"
            f"{item.current_failed_leads} | sales activities total/completed/open "
            f"{item.sales_activities_total}/{item.sales_activities_completed}/"
            f"{item.sales_activities_open} | confirmed communications "
            f"{item.confirmed_communications} | manager evidence "
            f"{item.manager_evidence_activities}"
        )
        if item.won_crm_opportunity_by_currency:
            amounts = ", ".join(
                f"{currency} {_money(amount)}"
                for currency, amount in item.won_crm_opportunity_by_currency
            )
            lines.append(f"  WON CRM OPPORTUNITY: {amounts}")

    lines.extend(["", "BUSINESS RULES STILL PENDING — DO NOT CALCULATE:"])
    for gap in snapshot.pending_business_rules:
        lines.append(f"• {gap.key}: {gap.status} — {gap.reason}")

    lines.extend(
        [
            "",
            "GUARDRAILS:",
            ("• facts come from crm_active_entities through deterministic semantic projections;"),
            ("• WON CRM OPPORTUNITY is CRM deal amount, not proven payment or accounting revenue;"),
            (
                "• current assignment/activity responsibility is not historical "
                "reassignment attribution;"
            ),
            (
                "• no First Response SLA compliance, stale/proposal verdict, manager "
                "score/rating, business conversion, plan/fact or escalation verdict "
                "is calculated;"
            ),
            (
                "• do not infer manager quality from these counts without an approved "
                "business policy."
            ),
        ]
    )
    return "\n".join(lines)
