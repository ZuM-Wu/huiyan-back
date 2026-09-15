# -*- coding: utf-8 -*-
"""产区管理演示插件管理员端接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_permission
from core.db.base import async_session_factory
from core.log.active_log import active_log
from core.response import ok
from plugins.addon.production_area_management.schemas import FeedbackCreate
from plugins.addon.production_area_management.services.demo_service import (
    DemoError,
    DemoService,
    serialize_log,
)

router = APIRouter(
    prefix="/api/admin/v1/plugins/production_area_management",
    tags=["产区管理演示插件"],
    dependencies=[Depends(check_admin)],
)


def _raise_demo_error(exc: DemoError) -> None:
    """将服务层业务异常映射为统一 HTTP 错误信封。"""
    raise HTTPException(status_code=exc.code, detail=exc.message) from exc


@router.get("/overview", dependencies=[Depends(require_permission("production_area_management:list"))])
async def get_overview():
    """读取产区、天气、积温与演示统计概览。"""
    async with async_session_factory() as db:
        return ok(await DemoService().get_overview(db))


@router.get("/logs", dependencies=[Depends(require_permission("production_area_management:list"))])
async def list_logs():
    """读取产区日志时间线。"""
    async with async_session_factory() as db:
        rows = await DemoService().list_logs(db)
    return ok({"list": [serialize_log(row) for row in rows]})


@router.post("/logs/generate", dependencies=[Depends(require_permission("production_area_management:log:generate"))])
async def generate_latest_log(request: Request):
    """生成唯一最新日志，重复请求返回既有结果。"""
    async with async_session_factory() as db:
        try:
            result = await DemoService().generate_log(db)
            await db.commit()
        except DemoError as exc:
            _raise_demo_error(exc)
    await active_log(
        f"生成产区日志：{result['log']['title']}",
        "production_area_management_log_generate",
        rel_id=result["log"]["id"], request=request,
    )
    return ok(result, msg="日志已生成" if result["created"] else "日志已存在")


@router.get("/tasks", dependencies=[Depends(require_permission("production_area_management:list"))])
async def list_tasks(status: str = Query("all", pattern="^(all|pending|completed)$", description="任务状态筛选")):
    """读取任务列表。"""
    async with async_session_factory() as db:
        rows = await DemoService().list_tasks(db, status)
    return ok({"list": rows})


@router.post("/tasks/generate", dependencies=[Depends(require_permission("production_area_management:task:generate"))])
async def generate_task(request: Request):
    """根据最新日志生成一条待办任务，重复请求保持幂等。"""
    assignee = getattr(request.state, "user_name", "") or "农技管理员"
    async with async_session_factory() as db:
        try:
            result = await DemoService().generate_task(db, assignee=assignee)
            await db.commit()
        except DemoError as exc:
            _raise_demo_error(exc)
    await active_log(
        f"AI生成产区任务：{result['task']['title']}",
        "production_area_management_task_generate",
        rel_id=result["task"]["id"], request=request,
    )
    return ok(result, msg="任务已生成" if result["created"] else "任务已存在")


@router.get("/tasks/{task_id}", dependencies=[Depends(require_permission("production_area_management:list"))])
async def get_task_detail(task_id: int):
    """读取任务详情、来源日志、MCP轨迹与反馈时间线。"""
    async with async_session_factory() as db:
        result = await DemoService().get_task_detail(db, task_id)
    if not result:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ok(result)


@router.post("/tasks/{task_id}/feedback", dependencies=[Depends(require_permission("production_area_management:feedback"))])
async def submit_feedback(task_id: int, data: FeedbackCreate, request: Request):
    """提交一次性任务反馈，完成后任务自动转为已完成。"""
    async with async_session_factory() as db:
        try:
            result = await DemoService().submit_feedback(
                db, task_id, data.result, data.content, data.metrics,
                admin_id=getattr(request.state, "user_id", 0),
                admin_name=getattr(request.state, "user_name", "") or "管理员",
            )
            await db.commit()
        except DemoError as exc:
            _raise_demo_error(exc)
    await active_log(
        f"提交产区任务反馈：{result['task']['title']}",
        "production_area_management_feedback", rel_id=task_id, request=request,
    )
    return ok(result, msg="反馈已提交")


@router.post("/demo/reset", dependencies=[Depends(require_permission("production_area_management:reset"))])
async def reset_demo(request: Request):
    """清理插件数据并恢复演示初始状态。"""
    async with async_session_factory() as db:
        result = await DemoService().reset_demo(db)
        await db.commit()
    await active_log("恢复产区管理演示初始状态", "production_area_management_reset", request=request)
    return ok(result, msg="演示初始状态已恢复")
