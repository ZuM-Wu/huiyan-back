# -*- coding: utf-8 -*-
"""任务查询服务

封装对 hy_task_log 和 hy_task_queue 表的查询与状态操作，
供 api 层和插件层调用。所有函数返回纯 dict/list，不返回 ORM 实例。

使用方式:
    from core.task_query_service import list_task_logs, get_task_overview
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, cast

from sqlalchemy import select, func, desc, update, false
from sqlalchemy.engine import CursorResult

from core.db.base import async_session_factory
from core.db.task_log import TaskLog
from core.db.task_queue import TaskQueue

logger = logging.getLogger(__name__)


# ---- 任务执行日志（hy_task_log）----

def _task_log_to_dict(log: TaskLog) -> dict:
    """将 TaskLog ORM 实例转为纯字典"""
    return {
        "id": log.id,
        "task_id": log.task_id,
        "owner": log.owner,
        "definition": log.definition,
        "attempt": log.attempt,
        "correlation_id": log.correlation_id or "",
        "event_id": log.event_id,
        "task_name": log.task_name,
        "task_desc": log.task_desc or log.task_name,
        "task_type": log.task_type,
        "status": log.status,
        "error_msg": log.error_msg or "",
        "duration_ms": log.duration_ms,
        "start_time": str(log.start_time) if log.start_time else "",
        "end_time": str(log.end_time) if log.end_time else "",
        "handle_status": log.handle_status,
        "handled_by": log.handled_by,
        "handled_by_name": log.handled_by_name or "",
        "handled_time": str(log.handled_time) if log.handled_time else "",
        "handle_note": log.handle_note or "",
        "retry_count": log.retry_count,
        "is_manual": log.is_manual,
        "create_time": str(log.create_time) if log.create_time else "",
    }


async def list_task_logs(
    page: int = 1,
    limit: int = 10,
    status: str = "",
    task_name: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """任务执行日志分页查询

    参数:
        page: 页码（从 1 开始）
        limit: 每页条数
        status: 状态筛选（success/failed）
        task_name: 任务名称筛选
        date_from: 开始日期（YYYY-MM-DD）
        date_to: 结束日期（YYYY-MM-DD，含当天）
    返回:
        {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    async with async_session_factory() as db:
        query = select(TaskLog)
        count_query = select(func.count(TaskLog.id))

        if status:
            query = query.where(TaskLog.status == status)
            count_query = count_query.where(TaskLog.status == status)

        if task_name:
            query = query.where(TaskLog.task_name == task_name)
            count_query = count_query.where(TaskLog.task_name == task_name)

        if date_from:
            try:
                sd = datetime.strptime(date_from, "%Y-%m-%d")
                query = query.where(TaskLog.create_time >= sd)
                count_query = count_query.where(TaskLog.create_time >= sd)
            except ValueError:
                pass

        if date_to:
            try:
                ed = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
                query = query.where(TaskLog.create_time < ed)
                count_query = count_query.where(TaskLog.create_time < ed)
            except ValueError:
                pass

        total = (await db.execute(count_query)).scalar() or 0

        query = (
            query.order_by(desc(TaskLog.id))
            .offset((page - 1) * limit)
            .limit(limit)
        )
        logs = (await db.execute(query)).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_task_log_to_dict(log) for log in logs],
    }


async def get_task_log(log_id: int) -> Optional[dict]:
    """查询单条任务日志详情

    参数:
        log_id: 日志ID
    返回:
        日志信息字典，不存在则返回 None
    """
    async with async_session_factory() as db:
        log = (await db.execute(
            select(TaskLog).where(TaskLog.id == log_id)
        )).scalar_one_or_none()
        if not log:
            return None
        return _task_log_to_dict(log)


async def retry_task(log_id: int) -> bool:
    """重试失败任务（委托 task_monitor 执行完整重试流程）

    内部调用 task_monitor.retry_task: 查询原日志 → 执行任务 → 标记已处理。

    参数:
        log_id: 日志ID
    返回:
        重试成功返回 True，失败返回 False
    """
    from services.task.task_monitor import task_monitor
    result = await task_monitor.retry_task(log_id, 0, "system")
    return result.get("success", False)


async def mark_task_handled(log_id: int, admin_id: int) -> bool:
    """标记任务日志为已处理

    参数:
        log_id: 日志ID
        admin_id: 操作管理员ID
    返回:
        标记成功返回 True，日志不存在返回 False
    """
    async with async_session_factory() as db:
        result = cast(CursorResult[Any], await db.execute(
            update(TaskLog).where(TaskLog.id == log_id).values(
                handle_status=1,
                handled_by=admin_id,
                handled_time=datetime.now(),
            )
        ))
        await db.commit()
        return (result.rowcount or 0) > 0


async def mark_task_ignored(log_id: int, admin_id: int) -> bool:
    """标记任务日志为已忽略

    参数:
        log_id: 日志ID
        admin_id: 操作管理员ID
    返回:
        标记成功返回 True，日志不存在返回 False
    """
    async with async_session_factory() as db:
        result = cast(CursorResult[Any], await db.execute(
            update(TaskLog).where(TaskLog.id == log_id).values(
                handle_status=2,
                handled_by=admin_id,
                handled_time=datetime.now(),
            )
        ))
        await db.commit()
        return (result.rowcount or 0) > 0


async def get_task_overview() -> dict:
    """任务状态概览统计

    返回: {today_total, today_success, today_failed, unhandled_count, tasks}
    """
    today_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async with async_session_factory() as db:
        today_total = await db.scalar(
            select(func.count(TaskLog.id)).where(
                TaskLog.create_time >= today_start
            )
        ) or 0

        today_success = await db.scalar(
            select(func.count(TaskLog.id)).where(
                TaskLog.create_time >= today_start,
                TaskLog.status == "success",
            )
        ) or 0

        today_failed = await db.scalar(
            select(func.count(TaskLog.id)).where(
                TaskLog.create_time >= today_start,
                TaskLog.status == "failed",
            )
        ) or 0

        unhandled_count = await db.scalar(
            select(func.count(TaskLog.id)).where(
                TaskLog.status == "failed",
                TaskLog.handle_status == 0,
            )
        ) or 0

        # 各任务最近一次执行状态（子查询取每组最大 id）
        subq = (
            select(
                TaskLog.task_name,
                func.max(TaskLog.id).label("max_id"),
            )
            .group_by(TaskLog.task_name)
            .subquery()
        )

        recent_logs = (await db.execute(
            select(TaskLog).join(
                subq,
                (TaskLog.id == subq.c.max_id)
                & (TaskLog.task_name == subq.c.task_name),
            ).order_by(desc(TaskLog.id))
        )).scalars().all()

        task_stats = []
        for log in recent_logs:
            fail_count_7d = await db.scalar(
                select(func.count(TaskLog.id)).where(
                    TaskLog.task_name == log.task_name,
                    TaskLog.status == "failed",
                    TaskLog.create_time >= today_start - timedelta(days=7),
                )
            ) or 0

            task_stats.append({
                "task_name": log.task_name,
                "task_desc": log.task_desc or log.task_name,
                "task_type": log.task_type,
                "last_status": log.status,
                "last_time": str(log.create_time) if log.create_time else "",
                "fail_count_7d": fail_count_7d,
            })

    return {
        "today_total": today_total,
        "today_success": today_success,
        "today_failed": today_failed,
        "unhandled_count": unhandled_count,
        "tasks": task_stats,
    }


# ---- 任务队列（hy_task_queue）----

def _task_queue_to_dict(t: TaskQueue, include_data: bool = False) -> dict:
    """将 TaskQueue ORM 实例转为纯字典

    参数:
        t: TaskQueue ORM 实例
        include_data: 是否包含 task_data（详情查询时为 True）
    """
    result = {
        "id": t.id,
        "type": t.type,
        "owner": t.owner,
        "definition": t.definition or t.type,
        "group": t.group_name,
        "status": t.status,
        "priority": t.priority,
        "retry": t.retry,
        "max_retry": t.max_retry,
        "attempt": t.attempt,
        "max_attempts": t.max_attempts,
        "idempotency_key": t.idempotency_key,
        "correlation_id": t.correlation_id or "",
        "event_id": t.event_id,
        "description": t.description or "",
        "error_msg": t.error_msg or "",
        "version": t.version,
        "start_time": str(t.start_time) if t.start_time else "",
        "finish_time": str(t.finish_time) if t.finish_time else "",
        "run_at": str(t.run_at) if t.run_at else "",
        "next_run_at": str(t.next_run_at) if t.next_run_at else "",
        "locked_at": str(t.locked_at) if t.locked_at else "",
        "create_time": str(t.create_time) if t.create_time else "",
    }
    if include_data:
        # 尝试解析 task_data 为 JSON 对象
        try:
            result["task_data"] = json.loads(t.task_data)
        except (json.JSONDecodeError, TypeError):
            result["task_data"] = t.task_data
    return result


async def list_task_queue(
    page: int = 1,
    limit: int = 10,
    status: str = "",
    task_type: str = "",
    keyword: str = "",
) -> dict:
    """任务队列分页查询

    参数:
        page: 页码（从 1 开始）
        limit: 每页条数
        status: 状态筛选（Wait/Exec/Paused/Dead/Cancelled；Finish 在执行日志展示）
        task_type: 任务类型筛选（notice/hook/自定义）
        keyword: 描述关键词筛选
    返回:
        {"total": int, "page": int, "limit": int, "list": [dict, ...]}
    """
    async with async_session_factory() as db:
        query = select(TaskQueue)
        count_query = select(func.count(TaskQueue.id))

        if not status:
            query = query.where(TaskQueue.status != "Finish")
            count_query = count_query.where(TaskQueue.status != "Finish")
        elif status == "Finish":
            # 已完成任务由执行日志承载，队列列表不提供历史完成项。
            query = query.where(false())
            count_query = count_query.where(false())
        else:
            query = query.where(TaskQueue.status == status)
            count_query = count_query.where(TaskQueue.status == status)

        if task_type:
            query = query.where(TaskQueue.definition == task_type)
            count_query = count_query.where(TaskQueue.definition == task_type)

        if keyword:
            query = query.where(TaskQueue.description.contains(keyword))
            count_query = count_query.where(TaskQueue.description.contains(keyword))

        total = (await db.execute(count_query)).scalar() or 0

        query = (
            query.order_by(desc(TaskQueue.id))
            .offset((page - 1) * limit)
            .limit(limit)
        )
        tasks = (await db.execute(query)).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "list": [_task_queue_to_dict(t) for t in tasks],
    }


async def get_task_queue_item(task_id: int) -> Optional[dict]:
    """查询单条队列任务详情（含 task_data）

    参数:
        task_id: 任务ID
    返回:
        任务信息字典，不存在则返回 None
    """
    async with async_session_factory() as db:
        t = (await db.execute(
            select(TaskQueue).where(TaskQueue.id == task_id)
        )).scalar_one_or_none()
        if not t:
            return None
        return _task_queue_to_dict(t, include_data=True)


async def retry_queue_task(task_id: int) -> bool:
    """重试 Dead 任务（重置尝试次数并重新进入 Wait）。

    参数:
        task_id: 任务ID
    返回:
        重置成功返回 True，任务不存在返回 False
    """
    async with async_session_factory() as db:
        t = (await db.execute(
            select(TaskQueue).where(TaskQueue.id == task_id)
        )).scalar_one_or_none()
        if not t or t.status != "Dead":
            return False

        await db.execute(
            update(TaskQueue)
            .where(TaskQueue.id == task_id)
            .values(
                status="Wait", retry=0, attempt=0, error_msg="",
                next_run_at=datetime.now(), locked_at=None,
                start_time=None, finish_time=None, version=t.version + 1,
            )
        )
        await db.commit()

    logger.info(f"[TaskQueryService] 队列任务已重置为待执行: id={task_id}")
    return True
