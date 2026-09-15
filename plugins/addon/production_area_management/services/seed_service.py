# -*- coding: utf-8 -*-
"""产区管理演示插件的安装播种与重置。"""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select

from plugins.addon.production_area_management.models import (
    ProductionAreaManagementFeedback,
    ProductionAreaManagementLog,
    ProductionAreaManagementTask,
)
from plugins.addon.production_area_management.services.demo_common import (
    build_mcp_trace,
    json_dump,
)


class SeedService:
    """管理演示初始数据和重置流程。"""

    async def seed_initial(self, db) -> dict[str, Any]:
        """播种三条历史日志、三条已完成任务和丰富反馈。"""
        count = (await db.execute(
            select(func.count(ProductionAreaManagementLog.id))
        )).scalar() or 0
        if count:
            return {"seeded": False}
        logs = self._build_logs()
        for row in logs:
            db.add(row)
        await db.flush()
        tasks = self._build_tasks(logs)
        for row in tasks:
            db.add(row)
        await db.flush()
        feedbacks = self._build_feedbacks(tasks)
        for row in feedbacks:
            db.add(row)
        await db.flush()
        return {
            "seeded": True,
            "log_count": len(logs),
            "task_count": len(tasks),
            "feedback_count": len(feedbacks),
        }

    async def reset_demo(self, db) -> dict[str, Any]:
        """清理插件数据并恢复演示初始状态。"""
        await db.execute(delete(ProductionAreaManagementFeedback))
        await db.execute(delete(ProductionAreaManagementTask))
        await db.execute(delete(ProductionAreaManagementLog))
        await db.flush()
        seeded = await self.seed_initial(db)
        return {"reset": True, **seeded}

    @staticmethod
    def _build_logs() -> list[ProductionAreaManagementLog]:
        """构造三条历史日志，明确标记为演示数据。"""
        return [
            ProductionAreaManagementLog(
                area_id=1, area_name="演示产区", title="降雨间歇完成田间排水巡查", stage="花芽膨大期",
                content="连续降雨后检查主排水沟和低洼地块，排水通畅，未出现根系积水。",
                weather_json=json_dump({"demo": True, "summary": "降雨间歇，田间湿度偏高"}),
                gdd_json=json_dump({"demo": True}), is_latest=0, is_seed=1,
                create_time=datetime(2026, 9, 10, 9, 20),
            ),
            ProductionAreaManagementLog(
                area_id=1, area_name="演示产区", title="土壤墒情与滴灌阀门复查", stage="花芽膨大期",
                content="抽查三处土壤墒情，滴灌阀门启闭正常，局部区域补灌 20 分钟。",
                weather_json=json_dump({"demo": True, "summary": "多云，蒸发量中等"}),
                gdd_json=json_dump({"demo": True}), is_latest=0, is_seed=1,
                create_time=datetime(2026, 9, 13, 10, 5),
            ),
            ProductionAreaManagementLog(
                area_id=1, area_name="演示产区", title="蚜虫复查与黄色诱虫板更换", stage="现蕾期",
                content="复查新梢蚜虫密度下降，更换 24 张黄色诱虫板，继续观察天敌数量。",
                weather_json=json_dump({"demo": True, "summary": "晴间多云，风力 2 级"}),
                gdd_json=json_dump({"demo": True}), is_latest=0, is_seed=1,
                create_time=datetime(2026, 9, 14, 15, 30),
            ),
        ]

    @staticmethod
    def _build_tasks(logs: list[ProductionAreaManagementLog]) -> list[ProductionAreaManagementTask]:
        """构造三条已完成任务。"""
        return [
            ProductionAreaManagementTask(
                log_id=logs[0].id, title="清理雨后排水沟并复核积水点",
                description="清理落叶和淤泥，复核低洼地块积水情况。",
                ai_summary="降雨后优先保障根系透气。",
                priority="high", assignee="张技术员", status="completed",
                plan_time=datetime(2026, 9, 10, 15, 0), completed_time=datetime(2026, 9, 10, 16, 40),
                mcp_trace=json_dump(build_mcp_trace({"name": "演示产区"}, {}, {})),
                is_seed=1, create_time=datetime(2026, 9, 10, 9, 30), update_time=datetime(2026, 9, 10, 16, 40),
            ),
            ProductionAreaManagementTask(
                log_id=logs[1].id, title="校准滴灌阀门并补测土壤墒情",
                description="完成阀门校准和三处墒情复测。",
                ai_summary="土壤水分处于临界区间。",
                priority="medium", assignee="李管理员", status="completed",
                plan_time=datetime(2026, 9, 13, 14, 0), completed_time=datetime(2026, 9, 13, 15, 20),
                mcp_trace=json_dump(build_mcp_trace({"name": "演示产区"}, {}, {})),
                is_seed=1, create_time=datetime(2026, 9, 13, 10, 15), update_time=datetime(2026, 9, 13, 15, 20),
            ),
            ProductionAreaManagementTask(
                log_id=logs[2].id, title="复查蚜虫密度并更换诱虫板",
                description="完成虫口复查和诱虫板更换。",
                ai_summary="蚜虫密度回落，但需继续监测。",
                priority="medium", assignee="王植保员", status="completed",
                plan_time=datetime(2026, 9, 14, 16, 30), completed_time=datetime(2026, 9, 14, 17, 10),
                mcp_trace=json_dump(build_mcp_trace({"name": "演示产区"}, {}, {})),
                is_seed=1, create_time=datetime(2026, 9, 14, 15, 45), update_time=datetime(2026, 9, 14, 17, 10),
            ),
        ]

    @staticmethod
    def _build_feedbacks(
        tasks: list[ProductionAreaManagementTask],
    ) -> list[ProductionAreaManagementFeedback]:
        """构造三条与任务一一对应的丰富反馈。"""
        return [
            ProductionAreaManagementFeedback(
                task_id=tasks[0].id, admin_id=1, admin_name="演示管理员", result="success",
                content="排水沟已清理，低洼点无持续积水，根系透气状况正常。",
                metrics_json=json_dump({"排水沟长度_m": 520, "积水点": 0}),
                create_time=datetime(2026, 9, 10, 16, 40),
            ),
            ProductionAreaManagementFeedback(
                task_id=tasks[1].id, admin_id=1, admin_name="演示管理员", result="partial",
                content="阀门校准完成，南侧地块墒情仍偏低，计划明天复测。",
                metrics_json=json_dump({"阀门检查数": 8, "土壤含水量_pct": 58}),
                create_time=datetime(2026, 9, 13, 15, 20),
            ),
            ProductionAreaManagementFeedback(
                task_id=tasks[2].id, admin_id=1, admin_name="演示管理员", result="success",
                content="蚜虫密度下降至每叶 0.8 头，诱虫板已全部更换。",
                metrics_json=json_dump({"蚜虫密度_头每叶": 0.8, "诱虫板张数": 24}),
                create_time=datetime(2026, 9, 14, 17, 10),
            ),
        ]
