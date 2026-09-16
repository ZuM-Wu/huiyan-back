# -*- coding: utf-8 -*-
"""产区管理按日事实同步的队列声明、周期投递与调度注册。"""

from __future__ import annotations

import logging

from core.db.base import async_session_factory
from core.time_utils import china_now
from services.task.definitions import TaskContext, TaskDefinition
from services.task.queue_worker import submit_task
from services.task.task_manager import task_manager

SCHEDULE_NAME = "production_area_management.daily_sync"
FACT_SYNC_TASK = "production_area_management.fact_sync"
DEFAULT_SCHEDULE_TIME = "06:00"
OWNER = "production_area_management"

logger = logging.getLogger(__name__)


async def run_sync() -> dict:
    """执行一次真实事实同步，只允许由队列处理器调用。"""
    from .fact_service import FactService

    async with async_session_factory() as db:
        result = await FactService().sync_logs(db)
        await db.commit()
    logger.info(
        "[production_area_management] 按日事实同步完成: 新增%s条，更新%s条",
        result.get("created_count", 0),
        result.get("updated_count", 0),
    )
    return result


async def fact_sync_handler(context: TaskContext, payload: dict) -> dict:
    """队列处理器：按日事实写入全部在队列内完成，超时、重试与日志由队列统一治理。"""
    del payload
    result = await run_sync()
    result["task_id"] = context.task_id
    return result


def task_definitions() -> list[TaskDefinition]:
    """声明队列任务定义，供插件 manifest 与运行时注册表使用。"""
    return [
        TaskDefinition(
            FACT_SYNC_TASK,
            "产区按日事实同步",
            OWNER,
            OWNER,
            fact_sync_handler,
            timeout_seconds=900,
            max_attempts=3,
            concurrency=1,
            backoff=True,
            failure_notifications=True,
        )
    ]


async def enqueue_scheduled_sync() -> int:
    """周期投递：同一自然日只入队一次，投递动作不占用调度执行体。"""
    return await submit_task(
        FACT_SYNC_TASK,
        {},
        description="产区按日事实同步",
        idempotency_key=f"{FACT_SYNC_TASK}:{china_now().date().isoformat()}",
    )


async def enqueue_manual_sync() -> int:
    """手动投递：管理员每次操作都入队独立任务，便于在任务队列页追溯执行结果。"""
    return await submit_task(
        FACT_SYNC_TASK,
        {},
        description="手动同步产区按日事实",
    )


def _parse_time(value: str | None) -> tuple[int, int]:
    """将计划时间解析为合法的小时和分钟。"""
    raw = str(value or DEFAULT_SCHEDULE_TIME)
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError):
        return 6, 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return 6, 0
    return hour, minute


async def register_schedule(enabled: bool = True, time_value: str = DEFAULT_SCHEDULE_TIME) -> None:
    """按配置幂等注册或移除每日投递任务。"""
    if not enabled:
        remove_schedule()
        return
    hour, minute = _parse_time(time_value)
    if task_manager.has_task(SCHEDULE_NAME):
        task_manager.reschedule_task(
            SCHEDULE_NAME,
            "cron",
            hour=hour,
            minute=minute,
        )
        return
    task_manager.register_task(
        SCHEDULE_NAME,
        enqueue_scheduled_sync,
        "cron",
        hour=hour,
        minute=minute,
        description="产区按日事实同步投递",
        task_type="plugin",
        emit_after_run=False,
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 3600},
    )


def remove_schedule() -> None:
    """移除每日投递任务，队列内已入队的任务保持可追溯。"""
    task_manager.remove_task(SCHEDULE_NAME)
