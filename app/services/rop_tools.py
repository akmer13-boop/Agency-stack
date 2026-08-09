from __future__ import annotations

from typing import Literal

from agents import FunctionTool, function_tool

from app.config import Settings
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
from app.services.rop_deep_analytics import (
    build_loss_report,
    build_manager_report,
    build_stage_aging_report,
    format_loss_report,
    format_manager_report,
    format_stage_aging_report,
)
from app.services.rop_mvp3 import (
    build_cycle_time_report,
    build_focus_report,
    build_stage_sla_report,
    format_cycle_time_report,
    format_focus_report,
    format_stage_sla_report,
)


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
        return format_loss_report(report)

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
        """Return manager scorecards by ASSIGNED_BY_ID from local synchronized CRM."""
        report = await build_manager_report(
            settings.database_path,
            timezone_name=settings.rop_timezone,
            attention_days=settings.rop_attention_days,
            critical_days=settings.rop_critical_days,
            included_category_ids=settings.rop_included_categories,
            excluded_stage_ids=settings.rop_excluded_stages,
        )
        return format_manager_report(
            report,
            min_closed_sample=settings.rop_manager_min_closed_sample,
        )

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
        return format_focus_report(report)

    @function_tool
    async def get_rop_deal(deal_id: int) -> str:
        """Return compact facts and stage evidence for one CRM deal ID.

        Use this tool for questions about a specific deal, for example deal 7040. It returns
        stage, amount, responsible manager, SLA state, activity timing, stage history and
        aggregate evidence about activities recorded after entry to the current stage.
        Raw timeline comments, activity descriptions and client contacts are intentionally
        excluded from the LLM tool output.

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
        base = format_deal_for_ai(report, timezone_name=settings.rop_timezone)
        stage_evidence = format_deal_stage_evidence_for_ai(
            report,
            evidence,
            timezone_name=settings.rop_timezone,
        )
        return f"{base}\n\n{stage_evidence}"

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
    ]
