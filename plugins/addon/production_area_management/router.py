# -*- coding: utf-8 -*-
"""产区管理插件管理员端接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.production_area_management.schemas import FeedbackCreate, LogImagesUpdate, ScheduleUpdate
from plugins.addon.production_area_management.services.demo_service import DemoService
from plugins.addon.production_area_management.services.fact_common import serialize_log
from plugins.addon.production_area_management.services.fact_service import FactError, FactService
from plugins.addon.production_area_management.services.scheduler import (
    SCHEDULE_NAME,
    enqueue_manual_sync,
    register_schedule,
)
from services.task.task_manager import task_manager

router = APIRouter(
    prefix="/api/admin/v1/plugins/production_area_management",
    tags=["产区管理插件"],
    dependencies=[Depends(check_admin)],
)

PLUGIN_NAME = "production_area_management"


def _raise_fact_error(exc: FactError) -> None:
    """将服务层业务异常映射为统一 HTTP 错误。"""
    raise HTTPException(status_code=exc.code, detail=exc.message) from exc


@router.get("/overview", dependencies=[Depends(require_permission("production_area_management:list"))])
async def get_overview():
    """读取当前产区、天气、积温和任务统计概览。"""
    async with async_session_factory() as db:
        return ok(await FactService().get_overview(db))


@router.get("/logs", dependencies=[Depends(require_permission("production_area_management:list"))])
async def list_logs():
    """读取当前产区按日事实日志。"""
    service = FactService()
    area = await service.resolve_target_area()
    async with async_session_factory() as db:
        rows = await service.list_logs(db, area["id"] if area else None)
    return ok({"list": [serialize_log(row) for row in rows]})


@router.get("/logs/{log_id}", dependencies=[Depends(require_permission("production_area_management:list"))])
async def get_log_detail(log_id: int):
    """读取单日事实、整改建议和已关联任务。"""
    async with async_session_factory() as db:
        result = await FactService().get_log_detail(db, log_id)
    if not result:
        raise HTTPException(status_code=404, detail="事实日志不存在")
    return ok(result)


@router.put("/logs/{log_id}/images", dependencies=[Depends(require_permission("production_area_management:log:generate"))])
async def update_log_images(log_id: int, data: LogImagesUpdate, request: Request):
    """保存管理员为指定日报补充的图片。"""
    async with async_session_factory() as db:
        try:
            log = await FactService().update_log_images(db, log_id, data.images)
            await db.commit()
        except FactError as exc:
            _raise_fact_error(exc)
    await active_log(
        f"更新产区日报图片：{log['title']}（{len(log['images'])} 张）",
        "production_area_management_log_images_update",
        rel_id=log_id,
        request=request,
    )
    return ok({"log": log}, msg="日报图片已保存")


@router.post("/schedule/run", dependencies=[Depends(require_permission("production_area_management:log:generate"))])
async def run_schedule_now(request: Request):
    """将一次立即同步投递到任务队列，执行结果在任务队列页查看。"""
    task_id = await enqueue_manual_sync()
    await active_log(
        f"手动同步产区按日事实日志（已入队任务 #{task_id}）",
        "production_area_management_log_sync",
        rel_id=task_id,
        request=request,
    )
    return ok({"task_id": task_id, "queued": True}, msg="产区事实同步已入队")


@router.get("/schedule", dependencies=[Depends(require_permission("production_area_management:list"))])
async def get_schedule():
    """读取每日事实同步计划和下一次执行时间。"""
    async with async_session_factory() as db:
        manager = ConfigManager()
        enabled = str(await manager.get(f"{PLUGIN_NAME}.schedule_enabled", db) or "1") == "1"
        time_value = await manager.get(f"{PLUGIN_NAME}.schedule_time", db) or "06:00"
    return ok({
        "enabled": enabled,
        "time": time_value,
        "registered": task_manager.has_task(SCHEDULE_NAME),
        "next_run_time": task_manager.next_run_time(SCHEDULE_NAME),
    })


@router.put("/schedule", dependencies=[Depends(require_permission("production_area_management:log:generate"))])
async def update_schedule(data: ScheduleUpdate, request: Request):
    """保存每日事实同步计划并立即更新运行时调度。"""
    async with async_session_factory() as db:
        manager = ConfigManager()
        await manager.set(
            f"{PLUGIN_NAME}.schedule_enabled",
            "1" if data.enabled else "0",
            db,
            description="产区管理按日事实同步配置",
        )
        await manager.set(
            f"{PLUGIN_NAME}.schedule_time",
            data.time,
            db,
            description="产区管理按日事实同步配置",
        )
    await register_schedule(data.enabled, data.time)
    await active_log(
        f"更新产区事实同步计划：{'启用' if data.enabled else '停用'} {data.time}",
        "production_area_management_schedule_update",
        request=request,
    )
    return await get_schedule()


@router.get("/tasks", dependencies=[Depends(require_permission("production_area_management:list"))])
async def list_tasks(status: str = Query("all", pattern="^(all|pending|completed)$", description="任务状态筛选")):
    """读取整改任务列表。"""
    async with async_session_factory() as db:
        rows = await FactService().list_tasks(db, status)
    return ok({"list": rows})


@router.post("/logs/{log_id}/task", dependencies=[Depends(require_permission("production_area_management:task:generate"))])
async def generate_task_for_log(log_id: int, request: Request):
    """根据指定事实日志的整改建议幂等创建整改任务。"""
    assignee = getattr(request.state, "user_name", "") or "农技管理员"
    async with async_session_factory() as db:
        try:
            result = await FactService().generate_task_for_log(db, log_id, assignee)
            await db.commit()
        except FactError as exc:
            _raise_fact_error(exc)
    await active_log(
        f"根据产区事实生成整改任务：{result['task']['title']}",
        "production_area_management_task_generate",
        rel_id=result["task"]["id"],
        request=request,
    )
    return ok(result, msg="整改任务已生成" if result["created"] else "整改任务已存在")


@router.post("/demo/reset", dependencies=[Depends(require_permission("production_area_management:log:generate"))])
async def reset_demo_status(request: Request):
    """重置 2026-09-17 演示日志卡片并清空其派生任务（演示重放用）。"""
    async with async_session_factory() as db:
        result = await DemoService().reset_demo_log(db)
        await db.commit()
    await active_log(
        f"重置产区演示状态：{result['fact_date']} 日志已恢复，清除任务 {result['removed_tasks']} 条",
        "production_area_management_demo_reset",
        rel_id=result["log_id"],
        request=request,
    )
    return ok(result, msg="演示状态已重置")


@router.get("/tasks/{task_id}", dependencies=[Depends(require_permission("production_area_management:list"))])
async def get_task_detail(task_id: int):
    """读取整改任务、来源事实和现场反馈时间线。"""
    async with async_session_factory() as db:
        result = await FactService().get_task_detail(db, task_id)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ok(result)


@router.post("/tasks/{task_id}/feedback", dependencies=[Depends(require_permission("production_area_management:feedback"))])
async def submit_feedback(task_id: int, data: FeedbackCreate, request: Request):
    """提交现场文字和图片反馈，只有完成结果才关闭任务。"""
    async with async_session_factory() as db:
        try:
            result = await FactService().submit_feedback(
                db,
                task_id,
                data.result,
                data.content,
                data.images,
                admin_id=getattr(request.state, "user_id", 0),
                admin_name=getattr(request.state, "user_name", "") or "管理员",
            )
            await db.commit()
        except FactError as exc:
            _raise_fact_error(exc)
    await active_log(
        f"提交产区任务现场反馈：{result['task']['title']}",
        "production_area_management_feedback",
        rel_id=task_id,
        request=request,
    )
    return ok(result, msg="反馈已提交")
