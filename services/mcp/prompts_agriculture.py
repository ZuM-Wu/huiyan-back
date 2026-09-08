"""农业主链 MCP Prompts。"""
from fastmcp.exceptions import PromptError, ToolError
from sqlalchemy import select

from core.db.base import async_session_factory
from core.db.production_area import AreaFarmer, PlantingBatch, ProductionArea
from services.mcp.tool_utils import _current_claims


async def _validate_scope(area_id: int, batch_id: int | None = None) -> None:
    """在返回提示模板前校验实体存在性与农户产区数据范围。"""
    try:
        claims = _current_claims()
    except ToolError as exc:
        raise PromptError(str(exc)) from exc

    async with async_session_factory() as db:
        area = (await db.execute(
            select(ProductionArea.id).where(ProductionArea.id == area_id)
        )).scalar_one_or_none()
        if area is None:
            raise PromptError(f"产区不存在: {area_id}")

        if claims.get("user_type") == "farmer":
            bound = (await db.execute(
                select(AreaFarmer.id).where(
                    AreaFarmer.area_id == area_id,
                    AreaFarmer.farmer_id == claims["user_id"],
                ).limit(1)
            )).scalar_one_or_none()
            if bound is None:
                raise PromptError("您未绑定该产区，无权生成相关提示")

        if batch_id is not None:
            batch = (await db.execute(
                select(PlantingBatch.id).where(
                    PlantingBatch.id == batch_id,
                    PlantingBatch.area_id == area_id,
                )
            )).scalar_one_or_none()
            if batch is None:
                raise PromptError("种植批次不存在或不属于该产区")


async def daily_agriculture_briefing(area_id: int) -> str:
    """生成产区每日农业简报任务模板。"""
    await _validate_scope(area_id)
    return (
        f"请为产区 {area_id} 生成今日农业简报。先调用 core_weather_snapshot 获取"
        "天气快照，再调用 core_weather_alerts 获取当前预警。输出必须包含：数据时间、"
        "客观天气事实、未来风险、可执行农事建议。事实与推断分开书写；数据缺失或"
        "时间过旧时明确说明，不得补造数据。"
    )


async def batch_growth_assessment(area_id: int, batch_id: int) -> str:
    """生成种植批次长势评估任务模板。"""
    await _validate_scope(area_id, batch_id)
    return (
        f"请评估产区 {area_id} 的种植批次 {batch_id} 当前长势。先调用 core_batch_gdd "
        "获取活动积温、有效积温和缺失天数，再调用 core_weather_daily 获取近期天气"
        "趋势。输出必须区分观测事实、推断结论和建议；说明数据缺口、统计时段及"
        "不确定性，不得把推断描述为已观测事实。"
    )


CORE_PROMPTS: list[dict] = [
    {
        "name": "daily_agriculture_briefing",
        "description": "结合天气快照与生效预警生成产区每日农业简报。",
        "handler": daily_agriculture_briefing,
        "audience": "both",
        "permission_code": "weather:view",
    },
    {
        "name": "batch_growth_assessment",
        "description": "结合积温与近期天气生成种植批次长势评估。",
        "handler": batch_growth_assessment,
        "audience": "both",
        "permission_code": "weather:view",
    },
]
