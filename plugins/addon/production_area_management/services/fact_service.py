# -*- coding: utf-8 -*-
"""产区管理插件的真实事实同步、整改任务与现场反馈服务。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
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
from plugins.addon.production_area_management.services.fact_common import (
    FactError,
    as_fact_date,
    build_fact_content,
    build_recommendation,
    calculate_gdd,
    json_dump,
    normalize_daily_weather,
    normalize_weather,
    parse_date,
    serialize_feedback,
    serialize_log,
    serialize_task,
)


class FactService:
    """按真实产区数据维护每日事实台账，并承载整改闭环。"""

    async def resolve_target_area(self) -> dict[str, Any] | None:
        """优先返回产区 1，否则返回第一条启用产区。"""
        areas = await list_area_brief()
        enabled = [item for item in areas if item.get("status") == 1]
        selected = next((item for item in enabled if item.get("id") == 1), None)
        selected = selected or (enabled[0] if enabled else None)
        if not selected:
            return None
        geo = await get_area_geo(int(selected["id"])) or {}
        create_time = geo.get("create_time")
        start_date = create_time.date() if isinstance(create_time, datetime) else create_time
        return {
            "id": int(selected["id"]),
            "name": selected.get("name", ""),
            "longitude": geo.get("longitude"),
            "latitude": geo.get("latitude"),
            "start_date": str(start_date or ""),
        }

    async def get_weather_snapshot(self, area_id: int) -> dict[str, Any] | None:
        """读取天气服务已缓存或已落库的实况，不在页面请求中伪造数据。"""
        result = await weather_service.get_weather(area_id, force=False)
        return normalize_weather(result)

    async def get_gdd_snapshot(
        self, area_id: int, start_date: date | None, end_date: date, db,
    ) -> dict[str, Any] | None:
        """根据逐日天气事实计算截至指定日期的积温。"""
        if not start_date or start_date > end_date:
            return None
        settings = await weather_service.get_settings(db)
        rows = await list_daily(area_id, start_date, end_date)
        result = calculate_gdd(
            rows,
            float(settings.get("gdd_base_temp", 10.0)),
            start_date,
            end_date,
        )
        result["start_date"] = str(start_date)
        result["end_date"] = str(end_date)
        return result

    async def get_context(self, db) -> dict[str, Any]:
        """汇合当前产区、实况天气和截至今日的积温。"""
        area = await self.resolve_target_area()
        if not area:
            return {
                "ready": False,
                "area": None,
                "weather": None,
                "gdd": None,
                "message": "本机未找到启用产区，请先检查产区管理。",
            }
        end_date = china_now().date()
        weather = await self.get_weather_snapshot(area["id"])
        gdd = await self.get_gdd_snapshot(
            area["id"], parse_date(area.get("start_date")), end_date, db,
        )
        messages: list[str] = []
        if not weather:
            messages.append("当前暂无可用天气快照")
        if not gdd:
            messages.append("产区缺少有效创建日期，暂无法计算积温")
        return {
            "ready": True,
            "area": area,
            "weather": weather,
            "gdd": gdd,
            "message": "；".join(messages),
        }

    async def sync_logs(
        self, db, area_id: int | None = None, end_date: date | None = None,
    ) -> dict[str, Any]:
        """从产区创建日起补齐每日事实，缺失天气只保存空事实行。"""
        areas = await self._areas_for_sync(area_id)
        end_date = end_date or china_now().date()
        settings = await weather_service.get_settings(db)
        results: list[dict[str, Any]] = []
        skipped: list[str] = []
        created_count = 0
        updated_count = 0
        for area in areas:
            geo = await get_area_geo(int(area["id"])) or {}
            start_date = as_fact_date(geo.get("create_time"))
            if not start_date:
                # 起算日只能来自产区创建日期；缺失时必须显式报错，
                # 不能让接口返回「新增 0 条」而掩盖事实台账的数据缺口。
                skipped.append(str(area.get("name") or area.get("id")))
                continue
            if start_date > end_date:
                continue
            daily_rows = await list_daily(int(area["id"]), start_date, end_date)
            daily_map = {
                parse_date(row.get("date")): row
                for row in daily_rows
                if parse_date(row.get("date"))
            }
            existing_rows = list((await db.execute(
                select(ProductionAreaManagementLog).where(
                    ProductionAreaManagementLog.area_id == int(area["id"]),
                    ProductionAreaManagementLog.fact_date.is_not(None),
                )
            )).scalars().all())
            existing_map = {row.fact_date: row for row in existing_rows}
            cumulative_rows: list[dict[str, Any]] = []
            cursor = start_date
            latest_row = None
            area_created_count = 0
            while cursor <= end_date:
                source_row = daily_map.get(cursor)
                weather = normalize_daily_weather(source_row)
                if source_row:
                    cumulative_rows.append(source_row)
                gdd = calculate_gdd(
                    cumulative_rows,
                    float(settings.get("gdd_base_temp", 10.0)),
                    start_date,
                    cursor,
                )
                row = existing_map.get(cursor)
                if row is None:
                    row = ProductionAreaManagementLog(
                        area_id=int(area["id"]),
                        area_name=area.get("name", ""),
                        fact_date=cursor,
                        is_seed=0,
                    )
                    db.add(row)
                    created_count += 1
                    area_created_count += 1
                else:
                    updated_count += 1
                row.area_name = area.get("name", "")
                row.title = f"{cursor} 产区事实"
                row.content = build_fact_content(cursor, weather, gdd)
                row.stage = ""
                row.weather_json = json_dump(weather)
                row.gdd_json = json_dump(gdd)
                row.recommendation = build_recommendation(weather)
                row.is_seed = 0
                row.is_latest = 0
                latest_row = row
                cursor += timedelta(days=1)
            for old_row in existing_rows:
                old_row.is_latest = 0
            if latest_row:
                latest_row.is_latest = 1
            results.append({
                "area_id": int(area["id"]),
                "area_name": area.get("name", ""),
                "start_date": str(start_date),
                "end_date": str(end_date),
                "created_count": area_created_count,
                "log_count": (end_date - start_date).days + 1,
            })
        if not results and skipped:
            raise FactError(
                409,
                "以下产区缺少创建日期，无法确定按日事实起算日："
                + "、".join(skipped)
                + "。请在产区管理中补齐创建日期后重试。",
            )
        await db.flush()
        return {
            "area_count": len(results),
            "created_count": created_count,
            "updated_count": updated_count,
            "skipped_areas": skipped,
            "end_date": str(end_date),
            "areas": results,
        }

    async def _areas_for_sync(self, area_id: int | None) -> list[dict[str, Any]]:
        """解析同步范围，避免把停用产区写入新的事实台账。"""
        areas = await list_area_brief()
        enabled = [item for item in areas if item.get("status") == 1]
        if area_id is not None:
            return [item for item in enabled if int(item["id"]) == area_id]
        return enabled

    async def list_logs(
        self, db, area_id: int | None = None,
    ) -> list[ProductionAreaManagementLog]:
        """按事实日期倒序返回真实日志，隐藏旧版空日期记录。"""
        query = select(ProductionAreaManagementLog).where(
            ProductionAreaManagementLog.fact_date.is_not(None),
            ProductionAreaManagementLog.is_seed == 0,
        )
        if area_id is not None:
            query = query.where(ProductionAreaManagementLog.area_id == area_id)
        return list((await db.execute(query.order_by(
            ProductionAreaManagementLog.fact_date.desc(),
            ProductionAreaManagementLog.id.desc(),
        ))).scalars().all())

    async def get_log_detail(self, db, log_id: int) -> dict[str, Any] | None:
        """读取单日事实及其整改任务。"""
        log = await db.get(ProductionAreaManagementLog, log_id)
        if not log or not log.fact_date or log.is_seed:
            return None
        task = (await db.execute(
            select(ProductionAreaManagementTask).where(
                ProductionAreaManagementTask.log_id == log_id,
            ).order_by(ProductionAreaManagementTask.id.desc()).limit(1)
        )).scalar_one_or_none()
        return {
            "log": serialize_log(log),
            "task": serialize_task(
                task,
                log.title,
                source_log_date=str(log.fact_date),
            ) if task else None,
        }

    async def get_task(self, db, task_id: int) -> ProductionAreaManagementTask | None:
        """按主键读取整改任务。"""
        return await db.get(ProductionAreaManagementTask, task_id)

    async def list_tasks(self, db, status: str = "all") -> list[dict[str, Any]]:
        """返回任务列表和最新反馈摘要。"""
        query = select(
            ProductionAreaManagementTask,
            ProductionAreaManagementLog.title,
            ProductionAreaManagementLog.fact_date,
        ).join(
            ProductionAreaManagementLog,
            ProductionAreaManagementLog.id == ProductionAreaManagementTask.log_id,
            isouter=True,
        ).where(ProductionAreaManagementTask.is_seed == 0)
        if status in {"pending", "completed"}:
            query = query.where(ProductionAreaManagementTask.status == status)
        rows = (await db.execute(query.order_by(
            ProductionAreaManagementTask.create_time.desc(),
            ProductionAreaManagementTask.id.desc(),
        ))).all()
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
            serialize_task(
                task,
                title or "",
                feedback_map.get(task.id, []),
                str(fact_date or ""),
            )
            for task, title, fact_date in rows
        ]

    async def get_task_detail(self, db, task_id: int) -> dict[str, Any] | None:
        """返回任务、来源事实和现场反馈时间线。"""
        task = await self.get_task(db, task_id)
        if not task or task.is_seed:
            return None
        log = await db.get(ProductionAreaManagementLog, task.log_id)
        feedbacks = list((await db.execute(
            select(ProductionAreaManagementFeedback).where(
                ProductionAreaManagementFeedback.task_id == task_id,
            ).order_by(
                ProductionAreaManagementFeedback.create_time.desc(),
                ProductionAreaManagementFeedback.id.desc(),
            )
        )).scalars().all())
        return {
            "task": serialize_task(
                task,
                log.title if log else "",
                feedbacks,
                str(log.fact_date or "") if log else "",
            ),
            "source_log": serialize_log(log) if log and log.fact_date else None,
            "feedbacks": [serialize_feedback(item) for item in feedbacks],
        }

    async def generate_task_for_log(
        self, db, log_id: int, assignee: str = "农技管理员",
    ) -> dict[str, Any]:
        """根据指定事实日志的整改建议创建幂等任务。"""
        log = await db.get(ProductionAreaManagementLog, log_id)
        if not log or not log.fact_date or log.is_seed:
            raise FactError(404, "事实日志不存在")
        existing = (await db.execute(
            select(ProductionAreaManagementTask).where(
                ProductionAreaManagementTask.log_id == log.id,
                ProductionAreaManagementTask.is_seed == 0,
            ).order_by(ProductionAreaManagementTask.id.desc()).limit(1)
        )).scalar_one_or_none()
        if existing:
            return {
                "created": False,
                "task": serialize_task(existing, log.title, source_log_date=str(log.fact_date)),
            }
        recommendation = log.recommendation or "暂无明显整改项，建议按日继续巡查并记录现场异常。"
        task = ProductionAreaManagementTask(
            log_id=log.id,
            title=f"整改：{log.fact_date}产区现场复核",
            description=recommendation,
            priority="high" if "加强" in recommendation or "复查" in recommendation else "medium",
            assignee=assignee or "农技管理员",
            status="pending",
            plan_time=china_now() + timedelta(days=1),
            is_seed=0,
            create_time=china_now(),
            update_time=china_now(),
        )
        db.add(task)
        await db.flush()
        return {
            "created": True,
            "task": serialize_task(task, log.title, source_log_date=str(log.fact_date)),
        }

    async def submit_feedback(
        self,
        db,
        task_id: int,
        result: str,
        content: str,
        images: list[str],
        admin_id: int,
        admin_name: str,
    ) -> dict[str, Any]:
        """保存现场反馈；只有明确完成才关闭任务。"""
        task = await self.get_task(db, task_id)
        if not task or task.is_seed:
            raise FactError(404, "任务不存在")
        if task.status == "completed":
            raise FactError(409, "该任务已完成，不能重复提交反馈")
        now = china_now()
        feedback = ProductionAreaManagementFeedback(
            task_id=task.id,
            admin_id=admin_id,
            admin_name=admin_name,
            result=result,
            content=content,
            images_json=json_dump(images or []),
            metrics_json="{}",
            create_time=now,
        )
        if result == "success":
            task.status = "completed"
            task.completed_time = now
        else:
            task.status = "pending"
            task.completed_time = None
        task.update_time = now
        db.add(feedback)
        await db.flush()
        return {
            "task": await self._serialized_task_with_feedback(db, task),
            "feedback": serialize_feedback(feedback),
        }

    async def _serialized_task_with_feedback(self, db, task) -> dict[str, Any]:
        """为反馈响应补齐来源日志和反馈数量。"""
        log = await db.get(ProductionAreaManagementLog, task.log_id)
        feedbacks = list((await db.execute(
            select(ProductionAreaManagementFeedback).where(
                ProductionAreaManagementFeedback.task_id == task.id,
            ).order_by(ProductionAreaManagementFeedback.create_time.desc())
        )).scalars().all())
        return serialize_task(
            task,
            log.title if log else "",
            feedbacks,
            str(log.fact_date or "") if log else "",
        )

    async def get_overview(self, db) -> dict[str, Any]:
        """返回当前产区事实、天气、积温和任务统计。"""
        context = await self.get_context(db)
        area_id = context["area"]["id"] if context["area"] else None
        logs = [serialize_log(row) for row in await self.list_logs(db, area_id)]
        tasks = await self.list_tasks(db, "all")
        latest = next((item for item in logs if item["is_latest"]), None)
        return {
            "readiness": {"ready": context["ready"], "message": context["message"]},
            "area": context["area"],
            "weather": context["weather"],
            "gdd": context["gdd"],
            "latest_log_exists": bool(latest),
            "latest_log_id": latest["id"] if latest else 0,
            "log_count": len(logs),
            "task_stats": {
                "total": len(tasks),
                "pending": sum(1 for item in tasks if item["status"] == "pending"),
                "completed": sum(1 for item in tasks if item["status"] == "completed"),
            },
        }


