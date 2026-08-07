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
        """Return the highest-priority active deals with 3+ and 5+ day movement risks."""
        return format_rop_risks(await _build_snapshot(settings))

    return [
        get_rop_period,
        get_rop_pipeline,
        get_rop_funnel,
        get_rop_risks,
    ]
