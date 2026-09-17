# -*- coding: utf-8 -*-
"""产区管理插件的演示状态维护。

插件本质用于产品演示：2026-09-17 的指定事实日志（潮湿天气 + 蓝莓败花期 +
人工振枝/捡花整改建议）会被按日同步按真实天气覆盖，这里提供把该日志恢复到
演示口径并清空其派生任务的重置能力，保证演示可以反复重放。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, select, update

from plugins.addon.production_area_management.models import (
    ProductionAreaManagementFeedback,
    ProductionAreaManagementLog,
    ProductionAreaManagementTask,
)
from plugins.addon.production_area_management.services.fact_common import (
    FactError,
    build_fact_content,
    json_dump,
    parse_date,
)
from plugins.addon.production_area_management.services.fact_service import FactService

# 演示日志固定指向 2026-09-17，与页面展示的“最新”卡片一致
DEMO_FACT_DATE = date(2026, 9, 17)
DEMO_STAGE = "蓝莓败花期"
# 潮湿天气事实：阴、高湿度、少量降水
DEMO_WEATHER = {
    "text_day": "阴",
    "temp_max": 25.5,
    "temp_min": 22.0,
    "temp_avg": 23.5,
    "humidity": 97.0,
    "precip": 4.6,
    "wind_scale": 2,
}
DEMO_STAGE_SENTENCE = "地块蓝莓进入败花期。"
DEMO_RECOMMENDATION = (
    "地块蓝莓进入败花期，叠加当日潮湿天气，残花易粘挂枝头并诱发灰霉病，"
    "建议人工介入整改，采取人工振枝或人工捡花，及时清理败落残花，并加强通风降湿。"
)


class DemoService:
    """维护演示日志卡片的可重放状态。"""

    async def reset_demo_log(self, db) -> dict[str, Any]:
        """恢复 2026-09-17 演示日志内容并删除其派生任务，仅影响该卡片。"""
        service = FactService()
        area = await service.resolve_target_area()
        if not area:
            raise FactError(409, "未找到启用产区，无法重置演示状态")
        start_date = parse_date(area.get("start_date"))
        fact_date = DEMO_FACT_DATE
        # 积温沿用真实天气累计，保证与概览同口径；起算日缺失时正文自动省略积温段
        gdd = {}
        if start_date and start_date <= fact_date:
            gdd = await service.get_gdd_snapshot(area["id"], start_date, fact_date, db) or {}
        row = (await db.execute(
            select(ProductionAreaManagementLog).where(
                ProductionAreaManagementLog.area_id == int(area["id"]),
                ProductionAreaManagementLog.fact_date == fact_date,
            )
        )).scalar_one_or_none()
        if row is None:
            row = ProductionAreaManagementLog(
                area_id=int(area["id"]),
                area_name=area["name"],
                fact_date=fact_date,
                is_seed=0,
            )
            db.add(row)
        row.area_name = area["name"]
        row.title = f"{fact_date} 产区事实"
        row.stage = DEMO_STAGE
        row.weather_json = json_dump(DEMO_WEATHER)
        row.gdd_json = json_dump(gdd)
        row.recommendation = DEMO_RECOMMENDATION
        row.content = build_fact_content(fact_date, DEMO_WEATHER, gdd) + DEMO_STAGE_SENTENCE
        # 先 flush 拿到主键，再清理其他行的最新标记，避免空值比较误伤全表
        await db.flush()
        row.is_latest = 1
        await db.execute(
            update(ProductionAreaManagementLog)
            .where(
                ProductionAreaManagementLog.area_id == int(area["id"]),
                ProductionAreaManagementLog.id != row.id,
            )
            .values(is_latest=0)
        )
        # 删除该日志派生的任务与反馈，让“根据建议生成整改任务”可以重放
        task_ids = list((await db.execute(
            select(ProductionAreaManagementTask.id).where(
                ProductionAreaManagementTask.log_id == row.id,
            )
        )).scalars().all())
        removed_feedbacks = 0
        if task_ids:
            removed_feedbacks = (await db.execute(
                delete(ProductionAreaManagementFeedback).where(
                    ProductionAreaManagementFeedback.task_id.in_(task_ids),
                )
            )).rowcount
            await db.execute(
                delete(ProductionAreaManagementTask).where(
                    ProductionAreaManagementTask.id.in_(task_ids),
                )
            )
        await db.flush()
        return {
            "log_id": row.id,
            "fact_date": str(fact_date),
            "removed_tasks": len(task_ids),
            "removed_feedbacks": removed_feedbacks,
        }
