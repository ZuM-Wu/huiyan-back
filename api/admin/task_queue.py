# -*- coding: utf-8 -*-
"""
任务队列管理 API

提供任务队列的查询、手动重试、删除、状态统计、配置读写等接口。
为系统核心功能，不依赖任何插件。
对标 task_monitor API 模式，路由前缀 /api/admin/v1/task-queue。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from datetime import datetime

from sqlalchemy import select, func, update

from core.auth.middleware_chain import check_admin
from core.auth.rbac import require_admin_permission
from core.db.base import async_session_factory
from core.db.task_queue import TaskQueue
from core.db.event_outbox import EventOutbox
from core.config_service import get_config, set_config
from core.task_query_service import list_task_queue, get_task_queue_item, retry_queue_task
from core.response import ok
from core.log.active_log import active_log

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/v1/task-queue",
    tags=["任务队列"],
    dependencies=[Depends(check_admin)],
)

# 队列配置项白名单（仅允许通过 API 修改这些 key）
_QUEUE_CONFIG_KEYS = [
    "task_queue_enabled",
    "task_queue_poll_interval",
    "task_queue_batch_size",
    "task_queue_clean_finish",
]


# ------------------------------------------------------------------
# 任务队列列表（固定路径，必须放在 /{task_id} 之前）
# ------------------------------------------------------------------
@router.get("/list")
async def list_tasks(
    task_type: str = Query("", description="任务定义筛选"),
    status: str = Query("", description="状态筛选: Wait/Exec/Paused/Dead/Cancelled"),
    keyword: str = Query("", description="关键词搜索（描述）"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(10, ge=1, le=100, description="每页条数"),
):
    """任务队列列表（分页 + 多条件筛选）"""
    result = await list_task_queue(
        page=page, limit=limit, status=status, task_type=task_type,
        keyword=keyword,
    )
    return ok({
        "list": result["list"],
        "total": result["total"],
        "page": page,
        "limit": limit,
    })


# ------------------------------------------------------------------
# 状态统计（固定路径，必须放在 /{task_id} 之前）
# ------------------------------------------------------------------
@router.get("/stats")
async def stats():
    """任务队列各运行状态统计。"""
    async with async_session_factory() as db:
        wait_count = await db.scalar(
            select(func.count(TaskQueue.id)).where(TaskQueue.status == "Wait")
        ) or 0
        exec_count = await db.scalar(
            select(func.count(TaskQueue.id)).where(TaskQueue.status == "Exec")
        ) or 0
        paused_count = await db.scalar(select(func.count(TaskQueue.id)).where(TaskQueue.status == "Paused")) or 0
        dead_count = await db.scalar(select(func.count(TaskQueue.id)).where(TaskQueue.status == "Dead")) or 0
        cancelled_count = await db.scalar(select(func.count(TaskQueue.id)).where(TaskQueue.status == "Cancelled")) or 0

    return ok({
        "wait": wait_count,
        "exec": exec_count,
        "paused": paused_count, "dead": dead_count, "cancelled": cancelled_count,
    })


@router.get("/definitions")
async def definitions():
    """返回机器可读任务定义目录。"""
    from services.task.definitions import task_registry
    return ok([{
        "name": item.name, "title": item.title, "owner": item.owner,
        "group": item.group, "timeout_seconds": item.timeout_seconds,
        "max_attempts": item.max_attempts, "concurrency": item.concurrency,
        "backoff": item.backoff,
        "failure_notifications": item.failure_notifications,
    } for item in task_registry.definitions()])


@router.get("/events")
async def events():
    """返回事件定义和订阅目录。"""
    from core.events import event_registry
    definitions_data = [{
        "name": item.name, "title": item.title, "category": item.category,
        "version": item.version, "owner": item.owner, "delivery": item.delivery,
        "replayable": item.replayable,
        "sensitive_fields": sorted(item.sensitive_fields),
        "export_fields": sorted(item.export_fields),
    } for item in event_registry.definitions()]
    subscriptions = [{
        "event_name": item.event_name, "owner": item.owner,
        "title": item.title, "timeout_seconds": item.timeout_seconds,
        "max_attempts": item.max_attempts, "concurrency": item.concurrency,
    } for item in event_registry.subscription_items()]
    return ok({"definitions": definitions_data, "subscriptions": subscriptions})


@router.get("/outbox")
async def outbox_list(
    status: str = Query(""), page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
):
    """可靠事件 Outbox 历史。"""
    async with async_session_factory() as db:
        query = select(EventOutbox)
        count = select(func.count(EventOutbox.id))
        if status:
            query = query.where(EventOutbox.status == status)
            count = count.where(EventOutbox.status == status)
        total = await db.scalar(count) or 0
        rows = (await db.execute(query.order_by(EventOutbox.id.desc())
                                  .offset((page - 1) * limit).limit(limit))).scalars().all()
    return ok({"total": total, "page": page, "limit": limit, "list": [{
        "id": row.id, "event_name": row.event_name, "event_version": row.event_version,
        "owner": row.owner, "status": row.status,
        "correlation_id": row.correlation_id or "",
        "create_time": str(row.create_time),
        "dispatched_at": str(row.dispatched_at) if row.dispatched_at else "",
    } for row in rows]})


@router.post("/outbox/{event_id}/replay", dependencies=[Depends(require_admin_permission("task_queue:replay"))])
async def replay_event(event_id: int, request: Request):
    """复制可重放可靠事件，原始记录保持不变。"""
    from core.events import event_registry
    async with async_session_factory() as db:
        source = (await db.execute(select(EventOutbox).where(EventOutbox.id == event_id))).scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail="事件不存在")
        definition = event_registry.require_definition(source.event_name)
        if not definition.replayable:
            raise HTTPException(status_code=409, detail="该事件不允许重放")
        clone = EventOutbox(
            event_name=source.event_name, event_version=source.event_version,
            owner=source.owner, payload=source.payload,
            correlation_id=source.correlation_id or f"replay:{source.id}",
            status="Pending", create_time=datetime.now(),
        )
        db.add(clone)
        await db.commit()
        await db.refresh(clone)
    await active_log(f"重放可靠事件: {event_id} -> {clone.id}", "task_queue_replay", rel_id=event_id, request=request)
    return ok({"event_id": clone.id}, msg="事件已进入待分发状态")


# ------------------------------------------------------------------
# 队列配置读写（固定路径，必须放在 /{task_id} 之前）
# ------------------------------------------------------------------
@router.get("/config")
async def get_queue_config():
    """读取队列配置（5个配置项）"""
    config = {}
    for key in _QUEUE_CONFIG_KEYS:
        val = await get_config(key)
        # 尝试转为 int（配置项均为数值或开关）
        try:
            config[key] = int(val) if val is not None else 0
        except (TypeError, ValueError):
            config[key] = val or ""

    return ok(config)


@router.put("/config", dependencies=[Depends(require_admin_permission("task_queue:config"))])
async def update_config(request: Request):
    """更新队列配置（仅允许白名单内的 key）"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体格式错误")

    updated_keys = []
    for key in _QUEUE_CONFIG_KEYS:
        if key in body:
            await set_config(key, str(body[key]))
            updated_keys.append(key)

    logger.info(f"[TaskQueue] 配置已更新: {updated_keys}")
    await active_log("更新任务队列配置", "task_queue_config", request=request)
    return ok(msg=f"已更新 {len(updated_keys)} 项配置")


# ------------------------------------------------------------------
# 任务详情 / 手动操作（参数路径，放在固定路径之后）
# ------------------------------------------------------------------
@router.get("/{task_id}")
async def get_task_detail(task_id: int):
    """任务详情（含 task_data JSON）"""
    task = await get_task_queue_item(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ok(task)


@router.post("/{task_id}/retry", dependencies=[Depends(require_admin_permission("task_queue:retry"))])
async def retry_task(task_id: int, request: Request):
    """手动重试 Dead 任务。"""
    success = await retry_queue_task(task_id)
    if not success:
        raise HTTPException(status_code=409, detail="任务不存在或不是死信")

    logger.info(f"[TaskQueue] 手动重试任务: id={task_id}")
    await active_log(f"重试队列任务: {task_id}", "task_queue_retry", rel_id=task_id, request=request)
    return ok(msg="任务已重置为待执行")


@router.post("/{task_id}/cancel", dependencies=[Depends(require_admin_permission("task_queue:cancel"))])
async def cancel_task(task_id: int, request: Request):
    """取消尚未执行的任务，保留历史记录。"""
    async with async_session_factory() as db:
        result = await db.execute(update(TaskQueue).where(
            TaskQueue.id == task_id, TaskQueue.status.in_(["Wait", "Paused"]),
        ).values(status="Cancelled", error_msg="管理员取消", version=TaskQueue.version + 1))
        if not result.rowcount:
            raise HTTPException(status_code=409, detail="任务不存在或当前状态不可取消")
        await db.commit()
    await active_log(f"取消队列任务: {task_id}", "task_queue_cancel", rel_id=task_id, request=request)
    return ok(msg="任务已取消")


@router.post("/{task_id}/resume", dependencies=[Depends(require_admin_permission("task_queue:resume"))])
async def resume_task(task_id: int, request: Request):
    """恢复一个暂停任务。"""
    async with async_session_factory() as db:
        result = await db.execute(update(TaskQueue).where(
            TaskQueue.id == task_id, TaskQueue.status == "Paused",
        ).values(status="Wait", next_run_at=datetime.now(), error_msg="", version=TaskQueue.version + 1))
        if not result.rowcount:
            raise HTTPException(status_code=409, detail="任务不存在或不是暂停状态")
        await db.commit()
    await active_log(f"恢复暂停任务: {task_id}", "task_queue_resume", rel_id=task_id, request=request)
    return ok(msg="任务已恢复")
