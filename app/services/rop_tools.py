from __future__ import annotations

from typing import Literal

from agents import FunctionTool, function_tool

from app.config import Settings
from app.services.rop_activity_risk import (
    build_activity_aware_risk,
    format_activity_aware_risk_for_ai,
)
from app.services.rop_analytics import (
    RopSnapshot,
    build_rop_snapshot,
    format_rop_funnel,
    format_rop_month,
    format_rop_pipeline,
    format_rop_risks,
    format_rop_today,
    format_rop_week,
)
from app.services.rop_deal import build_deal_drilldown, format_deal_for_ai
from app.services.rop_deal_evidence import (
    build_deal_stage_evidence,
    format_deal_stage_evidence_for_ai,
)
from app.services.rop_deal_vitality import (
    build_deal_vitality,
    format_deal_vitality_for_ai,
)
from app.services.rop_deep_analytics import (
    build_loss_report,
    build_manager_report,
    build_stage_aging_report,
    format_loss_report,
    format_manager_report,
    format_stage_aging_report,
)
from app.services.rop_directory import enrich_responsible_ids, load_rop_directory
from app.services.rop_leads import build_lead_intelligence, format_lead_intelligence_for_ai
from app.services.rop_mvp3 import (
    build_cycle_time_report,
    build_focus_report,
    build_stage_sla_report,
    format_cycle_time_report,
    format_focus_report,
    format_stage_sla_report,
)
from app.services.rop_recent_activity import (
    build_recent_deal_activity,
    format_recent_deal_activity_for_ai,
)
from app.services.rop_response_evidence import (
    build_lead_response_evidence_report,
    format_lead_response_evidence_for_ai,
)
from app.services.rop_weekend_leads import build_and_format_weekend_leads


async def _build_snapshot(settings: Settings) -> RopSnapshot:
    return await build_rop_snapshot(
        settings.database_path,
        attention_days=settings.rop_attention_days,
        critical_days=settings.rop_critical_days,
        risk_limit=settings.rop_risk_limit,
        timezone_name=settings.rop_timezone,
        included_category_ids=settings.rop_included_categories,
        excluded_stage_ids=settings.rop_excluded_stages,
    )


async def _enrich_with_directory(settings: Settings, text: str) -> str:
    directory = await load_rop_directory(settings.database_path)
    return enrich_responsible_ids(text, directory)


def build_rop_function_tools(settings: Settings) -> list[FunctionTool]:
    """Build read-only local analytics tools bound to the current runtime settings."""

    @function_tool
    async def get_rop_period(
        period: Literal["today", "week", "month"],
    ) -> str:
        """Return real local CRM KPI for today, the last 7 calendar days, or this month.

        Args:
            period: One of today, week, month.
        """
        snapshot = await _build_snapshot(settings)
        if period == "today":
            return format_rop_today(snapshot)
        if period == "week":
            return format_rop_week(snapshot)
        return format_rop_month(snapshot)

    @function_tool
    async def get_rop_pipeline() -> str:
        """Return the current active sales pipeline from the local synchronized CRM."""
        return format_rop_pipeline(await _build_snapshot(settings))

    @function_tool
    async def get_rop_funnel() -> str:
        """Return current deal distribution by named tourism pipelines and stages."""
        return format_rop_funnel(await _build_snapshot(settings))

    @function_tool
    async def get_rop_risks() -> str:
        """Return active deal movement-risk candidates using the configured 3+/5+ rule."""
        return format_rop_risks(await _build_snapshot(settings))

    @function_tool
    async def get_rop_losses() -> str:
        """Return actual final loss stages for deals lost during the current month."""
        report = await build_loss_report(
            settings.database_path,
            timezone_name=settings.rop_timezone,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        text = format_loss_report(report)
        return await _enrich_with_directory(settings, text)

    @function_tool
    async def get_rop_stage_aging() -> str:
        """Return active-deal stage aging with median age and 3+/5+ counts by stage."""
        report = await build_stage_aging_report(
            settings.database_path,
            attention_days=settings.rop_attention_days,
            critical_days=settings.rop_critical_days,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        return format_stage_aging_report(report)

    @function_tool
    async def get_rop_managers() -> str:
        """Return manager scorecards with local FIO/department when directory is synced."""
        report = await build_manager_report(
            settings.database_path,
            timezone_name=settings.rop_timezone,
            attention_days=settings.rop_attention_days,
            critical_days=settings.rop_critical_days,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        text = format_manager_report(
            report,
            min_closed_sample=settings.rop_manager_min_closed_sample,
        )
        return await _enrich_with_directory(settings, text)

    @function_tool
    async def get_rop_sla() -> str:
        """Return stage-specific SLA candidates for qualification and quote follow-up."""
        report = await build_stage_sla_report(
            settings.database_path,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        return format_stage_sla_report(report)

    @function_tool
    async def get_rop_cycle_time() -> str:
        """Return WON cycle time and qualification-to-quote timing from stage history."""
        report = await build_cycle_time_report(
            settings.database_path,
            timezone_name=settings.rop_timezone,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        return format_cycle_time_report(report)

    @function_tool
    async def get_rop_focus() -> str:
        """Return today's deterministic focus-list from business-confirmed SLA stages."""
        report = await build_focus_report(
            settings.database_path,
            limit=settings.rop_focus_limit,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        return await _enrich_with_directory(settings, format_focus_report(report))

    @function_tool
    async def get_rop_deal(deal_id: int) -> str:
        """Return compact facts, evidence, vitality and risk for one CRM deal ID.

        Use this tool for current status of a specific deal. It returns stage, amount,
        responsible manager, stage evidence, activity-aware risk and conservative deal
        vitality. Raw timeline comments, activity descriptions and client contacts are
        intentionally excluded from the LLM tool output.

        Args:
            deal_id: Numeric Bitrix24 deal ID.
        """
        report = await build_deal_drilldown(
            settings,
            deal_id,
            include_timeline_comments=False,
        )
        if report is None:
            return f"Сделка #{deal_id} не найдена в локальной синхронизированной CRM."
        evidence = await build_deal_stage_evidence(settings, report)
        risk = build_activity_aware_risk(report, evidence)
        vitality = build_deal_vitality(report, risk)
        base = format_deal_for_ai(report, timezone_name=settings.rop_timezone)
        stage_evidence = format_deal_stage_evidence_for_ai(
            report,
            evidence,
            timezone_name=settings.rop_timezone,
        )
        risk_text = format_activity_aware_risk_for_ai(risk)
        vitality_text = format_deal_vitality_for_ai(vitality)
        return f"{base}\n\n{stage_evidence}\n\n{risk_text}\n\n{vitality_text}"

    @function_tool
    async def get_rop_deal_activity(deal_id: int, days: int = 7) -> str:
        """Return exact local CRM activity counts for one deal over the last N days.

        Use this tool when the user asks what happened recently on a specific deal: last
        week, last 7/14/30 days, recent e-mails/calls/tasks, or whether any recent contact
        was recorded. The window is rolling N×24 hours. Unknown activity types are kept in
        total counts but are not treated as communications unless explicitly classified.

        Args:
            deal_id: Numeric Bitrix24 deal ID.
            days: Rolling lookback window from 1 to 365 days.
        """
        if days < 1 or days > 365:
            return "Период recent activity должен быть от 1 до 365 дней."
        report = await build_deal_drilldown(
            settings,
            deal_id,
            include_timeline_comments=False,
        )
        if report is None:
            return f"Сделка #{deal_id} не найдена в локальной синхронизированной CRM."
        activity = await build_recent_deal_activity(settings, report, days)
        return format_recent_deal_activity_for_ai(
            report,
            activity,
            timezone_name=settings.rop_timezone,
        )

    @function_tool
    async def get_rop_leads(days: int = 7) -> str:
        """Return Lead Intelligence for the rolling last N days.

        Use this tool for lead-focused questions: what happened with leads recently,
        current lead statuses, successful/failed lead finalizations, lead aging, sources,
        lead CRM activity, and manager lead workload. It does not infer lead-to-deal cohort
        conversion by dividing new deals by new leads.

        Args:
            days: Rolling lookback window from 1 to 365 days.
        """
        if days < 1 or days > 365:
            return "Период Lead Intelligence должен быть от 1 до 365 дней."
        report = await build_lead_intelligence(settings, days)
        directory = await load_rop_directory(settings.database_path)
        return format_lead_intelligence_for_ai(
            report,
            directory,
            timezone_name=settings.rop_timezone,
        )

    @function_tool
    async def get_rop_lead_response_evidence(days: int = 7) -> str:
        """Return observed lead response evidence for the rolling last N days.

        Use this tool when the user asks how quickly new leads receive a first
        observable manager-side action or confirmed CRM communication. The tool
        returns calendar elapsed evidence only. It does not calculate First
        Response SLA compliance, business-hours timing, or manager ranking.

        Args:
            days: Rolling cohort lookback from 1 to 365 days.
        """
        if days < 1 or days > 365:
            return "Период response evidence должен быть от 1 до 365 дней."
        report = await build_lead_response_evidence_report(settings, days)
        return format_lead_response_evidence_for_ai(report)

    @function_tool
    async def get_rop_weekend_leads() -> str:
        """Return exact lead cohort and manager processing facts for the weekend.

        Use this tool for questions such as "how many leads came over the weekend" and
        "how did managers process weekend leads". The cohort window is calendar Saturday
        and Sunday in ROP_TIMEZONE. Processing is observed from each lead creation through
        the current report time. The tool returns only aggregated evidence and never calls
        the observed first confirmed CRM communication a first-response SLA.
        """
        return await build_and_format_weekend_leads(settings)

    return [
        get_rop_period,
        get_rop_pipeline,
        get_rop_funnel,
        get_rop_risks,
        get_rop_losses,
        get_rop_stage_aging,
        get_rop_managers,
        get_rop_sla,
        get_rop_cycle_time,
        get_rop_focus,
        get_rop_deal,
        get_rop_deal_activity,
        get_rop_leads,
        get_rop_lead_response_evidence,
        get_rop_weekend_leads,
    ]
