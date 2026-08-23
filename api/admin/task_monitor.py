# -*- coding: utf-8 -*-
"""
任务监控 API（核心路由）

提供任务执行日志查询、手动重试、标记处理、状态概览等接口。
为系统核心功能，不依赖任何插件。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.task_query_service import (
    list_task_logs,
    get_task_log,
    get_task_overview,
)
from services.task.task_monitor import task_monitor
from core.response import ok
from core.log.active_log import active_log

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/v1/task-monitor",
    tags=["任务监控"],
    dependencies=[Depends(check_admin)],
)


# ------------------------------------------------------------------
# 任务执行日志
# ------------------------------------------------------------------
@router.get("/logs")
async def list_logs(
    *,
    task_name: str = Query("", description="任务名称筛选"),
    status: str = Query("", description="状态筛选: success/failed"),
    handle_status: str = Query("", description="处理状态: 0/1/2"),
    task_type: str = Query("", description="任务类型: system/weather/notice/plugin"),
    keyword: str = Query("", description="关键词搜索（任务描述）"),
    start_date: str = Query("", description="开始日期 YYYY-MM-DD"),
    end_date: str = Query("", description="结束日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """任务执行日志列表（分页 + 多条件筛选）"""
    result = await list_task_logs(
        page=page,
        limit=limit,
        status=status,
        task_name=task_name,
        date_from=start_date,
        date_to=end_date,
    )

    items = result["list"]

    # 服务层不支持的筛选条件在 API 层内存过滤
    if handle_status != "":
        hs = int(handle_status)
        items = [it for it in items if it["handle_status"] == hs]
    if task_type:
        items = [it for it in items if it["task_type"] == task_type]
    if keyword:
        kw = keyword.lower()
        items = [it for it in items if kw in (it["task_desc"] or "").lower()]

    return ok({
        "list": items,
        "total": len(items) if (handle_status or task_type or keyword) else result["total"],
        "page": page,
        "limit": limit,
    })


@router.get("/logs/{log_id}")
async def get_log_detail(log_id: int):
    """日志详情"""
    log = await get_task_log(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="日志不存在")
    return ok(log)


@router.post("/logs/{log_id}/retry", dependencies=[Depends(require_admin_permission("task_monitor:retry"))])
async def retry_log(log_id: int, request: Request):
    """手动重试失败任务"""
    admin_id = getattr(request.state, "user_id", 0)
    admin_name = getattr(request.state, "user_name", "")
    result = await task_monitor.retry_task(log_id, admin_id, admin_name)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("msg", "重试失败"))
    await active_log(f"重试任务日志: {log_id}", "task_monitor_retry", rel_id=log_id, request=request)
    return ok(msg=result["msg"])


@router.post("/logs/{log_id}/handle", dependencies=[Depends(require_admin_permission("task_monitor:handle"))])
async def handle_log(log_id: int, request: Request):
    """标记已处理"""
    admin_id = getattr(request.state, "user_id", 0)
    admin_name = getattr(request.state, "user_name", "")
    note = ""
    try:
        body = await request.json()
        note = body.get("note", "")
    except Exception:
        pass
    result = await task_monitor.mark_handled(log_id, admin_id, admin_name, note)
    await active_log(f"标记任务日志已处理: {log_id}", "task_monitor_handle", rel_id=log_id, request=request)
    return ok(msg=result["msg"])


@router.post("/logs/{log_id}/ignore", dependencies=[Depends(require_admin_permission("task_monitor:ignore"))])
async def ignore_log(log_id: int, request: Request):
    """标记已忽略"""
    admin_id = getattr(request.state, "user_id", 0)
    admin_name = getattr(request.state, "user_name", "")
    note = ""
    try:
        body = await request.json()
        note = body.get("note", "")
    except Exception:
        pass
    result = await task_monitor.mark_ignored(log_id, admin_id, admin_name, note)
    await active_log(f"忽略任务日志: {log_id}", "task_monitor_ignore", rel_id=log_id, request=request)
    return ok(msg=result["msg"])


# ------------------------------------------------------------------
# 状态概览
# ------------------------------------------------------------------
@router.get("/status")
async def status_overview():
    """任务状态概览（今日统计 + 未处理告警数 + 各任务最近执行状态）"""
    data = await get_task_overview()
    return ok(data)
