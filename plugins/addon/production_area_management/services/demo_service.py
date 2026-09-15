# -*- coding: utf-8 -*-
"""产区管理演示插件的固定流程与真实数据编排。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from core.production_area_service import get_area_geo, list_area_brief
from core.time_utils import china_now
from core.weather_gdd import list_daily
from core.weather_service import weather_service
from plugins.addon.production_area_management.models import (
    ProductionAreaManagementFeedback,
    ProductionAreaManagementLog,
    ProductionAreaManagementTask,
)
from plugins.addon.production_area_management.services.demo_common import (
    DEMO_BATCH_NO,
    DEMO_PLANT_DATE,
    LATEST_LOG_TITLE,
    TASK_TITLE,
    DemoError,
    build_mcp_trace,
    calculate_gdd,
    json_dump,
    json_load,
    normalize_weather,
    serialize_log,
    serialize_task,
)
from plugins.addon.production_area_management.services.seed_service import SeedService


class DemoService:
    """产区管理演示的数据读取、日志生成、任务生成和反馈服务。"""

    def __init__(self):
        self._seed_service = SeedService()

    async def resolve_demo_area(self) -> dict[str, Any] | None:
        """优先产区 1，缺失时回退第一条启用产区。"""
        areas = await list_area_brief()
        enabled = [item for item in areas if item.get("status") == 1]
        selected = next((item for item in enabled if item.get("id") == 1), None)
        if selected is None and enabled:
            selected = enabled[0]
        if selected is None:
            return None
        geo = await get_area_geo(int(selected["id"])) or {}
        return {
            "id": int(selected["id"]),
            "name": selected.get("name", ""),
            "province": geo.get("province", ""),
            "city": geo.get("city", ""),
            "district": geo.get("district", ""),
            "longitude": geo.get("longitude", 0),
            "latitude": geo.get("latitude", 0),
        }

    async def get_weather_snapshot(self, area_id: int) -> dict[str, Any]:
        """读取真实天气快照，不强制触发第三方实时请求。"""
        result = await weather_service.get_weather(area_id, force=False)
        weather = normalize_weather(result)
        if weather is None:
            message = (result or {}).get("error_msg") or "本机暂无可用天气快照"
            raise DemoError(422, f"天气数据不可用：{message}")
        return weather

    async def get_gdd_snapshot(self, area_id: int, db) -> dict[str, Any]:
        """读取系统基点温度并累计演示批次的真实逐日历史。"""
        settings = await weather_service.get_settings(db)
        base_temp = float(settings.get("gdd_base_temp", 10.0))
        end = china_now().date()
        rows = await list_daily(area_id, DEMO_PLANT_DATE, end)
        result = calculate_gdd(rows, base_temp, DEMO_PLANT_DATE, end)
        if result["counted_days"] <= 0:
            raise DemoError(422, "本机暂无可用逐日天气数据，无法计算积温")
        result["batch_no"] = DEMO_BATCH_NO
        result["crop_name"] = "蓝莓"
        return result

    async def get_context(self, db) -> dict[str, Any]:
        """汇合真实产区、天气和积温，供概览与日志生成复用。"""
        area = await self.resolve_demo_area()
        if not area:
            return {
                "ready": False,
                "area": None,
                "weather": None,
                "gdd": None,
                "message": "本机未找到启用产区，请先检查产区管理。",
            }
        weather = None
        gdd = None
        errors: list[str] = []
        try:
            weather = await self.get_weather_snapshot(area["id"])
        except Exception as exc:
            errors.append(str(exc))
        try:
            gdd = await self.get_gdd_snapshot(area["id"], db)
        except Exception as exc:
            errors.append(str(exc))
        return {
            "ready": bool(weather and gdd),
            "area": area,
            "weather": weather,
            "gdd": gdd,
            "message": "；".join(errors),
        }

    async def get_latest_log(self, db) -> ProductionAreaManagementLog | None:
        """读取插件内唯一的最新生成日志。"""
        return (await db.execute(
            select(ProductionAreaManagementLog)
            .where(ProductionAreaManagementLog.is_latest == 1)
            .order_by(ProductionAreaManagementLog.id.desc())
            .limit(1)
        )).scalar_one_or_none()

    async def list_logs(self, db) -> list[ProductionAreaManagementLog]:
        """按最新日志优先、创建时间倒序返回演示日志。"""
        return list((await db.execute(
            select(ProductionAreaManagementLog).order_by(
                ProductionAreaManagementLog.is_latest.desc(),
                ProductionAreaManagementLog.create_time.desc(),
                ProductionAreaManagementLog.id.desc(),
            )
        )).scalars().all())

    async def get_task(self, db, task_id: int) -> ProductionAreaManagementTask | None:
        """按主键读取插件任务。"""
        return await db.get(ProductionAreaManagementTask, task_id)

    async def get_generated_task(self, db) -> ProductionAreaManagementTask | None:
        """读取 AI 生成且非种子的任务，用于幂等控制。"""
        return (await db.execute(
            select(ProductionAreaManagementTask)
            .where(ProductionAreaManagementTask.is_seed == 0)
            .order_by(ProductionAreaManagementTask.id.desc())
            .limit(1)
        )).scalar_one_or_none()

    async def generate_log(self, db) -> dict[str, Any]:
        """生成唯一最新日志；已生成时直接返回既有结果。"""
        existing = await self.get_latest_log(db)
        if existing:
            return {"created": False, "log": serialize_log(existing)}
        context = await self.get_context(db)
        if not context["ready"]:
            raise DemoError(422, context["message"] or "真实产区或天气数据不可用")
        area, weather, gdd = context["area"], context["weather"], context["gdd"]
        region = "".join(filter(None, [area.get("province"), area.get("city"), area.get("district")]))
        content = (
            f"{region}{area['name']}蓝莓进入始花期。当前{weather['text']}，"
            f"气温 {weather['temp']}℃，相对湿度 {weather['humidity']}%，"
            f"{weather['wind_dir']}{weather['wind_scale']}级。自 {gdd['start_date']} 起累计"
            f" {gdd['counted_days']} 天，活动积温 {gdd['active_gdd']}℃，"
            f"有效积温 {gdd['effective_gdd']}℃。建议关注授粉窗口、避雨通风和蜂箱布置。"
        )
        row = ProductionAreaManagementLog(
            area_id=area["id"], area_name=area["name"], title=LATEST_LOG_TITLE,
            content=content, stage="始花期", weather_json=json_dump(weather),
            gdd_json=json_dump(gdd), is_latest=1, is_seed=0, create_time=china_now(),
        )
        db.add(row)
        await db.flush()
        return {"created": True, "log": serialize_log(row)}

    async def list_tasks(self, db, status: str = "all") -> list[dict[str, Any]]:
        """返回任务列表，并附带来源日志标题与最新反馈摘要。"""
        query = select(ProductionAreaManagementTask, ProductionAreaManagementLog.title).join(
            ProductionAreaManagementLog,
            ProductionAreaManagementLog.id == ProductionAreaManagementTask.log_id,
            isouter=True,
        )
        if status in {"pending", "completed"}:
            query = query.where(ProductionAreaManagementTask.status == status)
        rows = (await db.execute(
            query.order_by(
                ProductionAreaManagementTask.create_time.desc(),
                ProductionAreaManagementTask.id.desc(),
            )
        )).all()
        feedback_rows = list((await db.execute(
            select(ProductionAreaManagementFeedback).order_by(
                ProductionAreaManagementFeedback.create_time.desc(),
                ProductionAreaManagementFeedback.id.desc(),
            )
        )).scalars().all())
        feedback_map: dict[int, list[ProductionAreaManagementFeedback]] = {}
        for feedback in feedback_rows:
            feedback_map.setdefault(feedback.task_id, []).append(feedback)
        return [
            serialize_task(task, title or "", feedback_map.get(task.id, []))
            for task, title in rows
        ]

    async def get_task_detail(self, db, task_id: int) -> dict[str, Any] | None:
        """返回任务、来源日志和完整反馈时间线。"""
        task = await self.get_task(db, task_id)
        if not task:
            return None
        log = await db.get(ProductionAreaManagementLog, task.log_id)
        feedbacks = list((await db.execute(
            select(ProductionAreaManagementFeedback)
            .where(ProductionAreaManagementFeedback.task_id == task_id)
            .order_by(
                ProductionAreaManagementFeedback.create_time.desc(),
                ProductionAreaManagementFeedback.id.desc(),
            )
        )).scalars().all())
        return {
            "task": serialize_task(task, log.title if log else "", feedbacks),
            "source_log": serialize_log(log) if log else None,
            "feedbacks": [self._serialize_feedback(item) for item in feedbacks],
        }

    async def generate_task(self, db, assignee: str = "农技管理员") -> dict[str, Any]:
        """按最新日志生成一条待办任务；重复请求只返回既有任务。"""
        latest = await self.get_latest_log(db)
        if not latest:
            raise DemoError(409, "请先生成最新产区日志，再生成任务")
        existing = await self.get_generated_task(db)
        if existing:
            return {"created": False, "task": serialize_task(existing, latest.title)}
        area = {"id": latest.area_id, "name": latest.area_name}
        weather = json_load(latest.weather_json, {})
        gdd = json_load(latest.gdd_json, {})
        trace = build_mcp_trace(area, weather, gdd)
        task = ProductionAreaManagementTask(
            log_id=latest.id,
            title=TASK_TITLE,
            description="检查蓝莓花期授粉条件、蜂箱位置、避雨通风和田间操作窗口。",
            ai_summary=(
                f"{latest.area_name}蓝莓进入始花期，当前气温 {weather.get('temp', '-')}℃、"
                f"湿度 {weather.get('humidity', '-')}%，有效积温 {gdd.get('effective_gdd', '-')}℃。"
                "建议在天气稳定、花粉活力较好的时段完成蜂箱布置和授粉条件巡查。"
            ),
            priority="high", assignee=assignee, status="pending",
            plan_time=china_now() + timedelta(days=1),
            mcp_trace=json_dump(trace), is_seed=0,
            create_time=china_now(), update_time=china_now(),
        )
        db.add(task)
        await db.flush()
        return {"created": True, "task": serialize_task(task, latest.title)}

    async def submit_feedback(
        self,
        db,
        task_id: int,
        result: str,
        content: str,
        metrics: dict[str, Any],
        admin_id: int,
        admin_name: str,
    ) -> dict[str, Any]:
        """提交一次性反馈，并自动将任务流转为已完成。"""
        task = await self.get_task(db, task_id)
        if not task:
            raise DemoError(404, "任务不存在")
        if task.status == "completed":
            raise DemoError(409, "该任务已有反馈，不能重复提交")
        feedback = ProductionAreaManagementFeedback(
            task_id=task.id, admin_id=admin_id, admin_name=admin_name,
            result=result, content=content, metrics_json=json_dump(metrics),
            create_time=china_now(),
        )
        task.status = "completed"
        task.completed_time = china_now()
        task.update_time = china_now()
        db.add(feedback)
        await db.flush()
        return {
            "task": serialize_task(task),
            "feedback": {
                "id": feedback.id,
                "task_id": feedback.task_id,
                "admin_id": feedback.admin_id,
                "admin_name": feedback.admin_name or "",
                "result": feedback.result,
                "content": feedback.content,
                "metrics": json_load(feedback.metrics_json, {}),
                "create_time": str(feedback.create_time or ""),
            },
        }

    async def get_overview(self, db) -> dict[str, Any]:
        """返回页面首屏所需的产区和统计概览。"""
        context = await self.get_context(db)
        logs = [serialize_log(row) for row in await self.list_logs(db)]
        tasks = await self.list_tasks(db, "all")
        latest = next((item for item in logs if item["is_latest"]), None)
        generated = next((item for item in tasks if not item["is_seed"]), None)
        return {
            "readiness": {
                "ready": context["ready"],
                "message": context["message"],
            },
            "area": context["area"],
            "weather": context["weather"],
            "gdd": context["gdd"],
            "latest_log_exists": bool(latest),
            "latest_log_id": latest["id"] if latest else 0,
            "generated_task_exists": bool(generated),
            "generated_task_id": generated["id"] if generated else 0,
            "log_count": len(logs),
            "task_stats": {
                "total": len(tasks),
                "pending": sum(1 for item in tasks if item["status"] == "pending"),
                "completed": sum(1 for item in tasks if item["status"] == "completed"),
            },
        }

    async def seed_initial(self, db) -> dict[str, Any]:
        """供插件安装调用，委托发布用种子数据服务。"""
        return await self._seed_service.seed_initial(db)

    async def reset_demo(self, db) -> dict[str, Any]:
        """仅供演示菜单调用，恢复日志、任务和反馈初始状态。"""
        return await self._seed_service.reset_demo(db)

    @staticmethod
    def _serialize_feedback(row: ProductionAreaManagementFeedback) -> dict[str, Any]:
        """序列化反馈记录，避免业务文件继续膨胀。"""
        return {
            "id": row.id,
            "task_id": row.task_id,
            "admin_id": row.admin_id,
            "admin_name": row.admin_name or "",
            "result": row.result,
            "content": row.content,
            "metrics": json_load(row.metrics_json, {}),
            "create_time": str(row.create_time or ""),
        }
