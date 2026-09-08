# -*- coding: utf-8 -*-
"""AI 供应商连接的周期健康检查任务。"""
from __future__ import annotations

import logging
from datetime import datetime
from core.time_utils import china_now

from sqlalchemy import select

from core.db.ai_resources import AgentScopeConnectionPoolModel
from core.db.base import async_session_factory
from services.task.definitions import TaskDefinition, task_registry
from services.task.task_manager import task_manager

logger = logging.getLogger(__name__)

TASK_DEFINITION = "ai.connection.health_check"
SCHEDULE_NAME = "ai_connection_health_schedule"
INTERVAL_MINUTES = 5


async def handle_connection_health(context, task_data: dict) -> None:
    """队列处理器：执行一次连接检测并由健康服务落库。"""
    del context
    connection_id = int(task_data.get("connection_id") or 0)
    if connection_id <= 0:
        raise ValueError("连接健康检查缺少 connection_id")
    from services.agentscope.connection_health import test_connection_record

    result = await test_connection_record(connection_id)
    if result.skipped:
        logger.info("AI 连接自动检测已跳过: connection_id=%s", connection_id)
    elif not result.success:
        logger.warning(
            "AI 连接自动检测失败: connection_id=%s reason=%s",
            connection_id,
            result.message,
        )


# 任务声明与处理器放在同一模块，确保调度接入和启动接入使用完全相同的契约。
TASK_DECLARATION = TaskDefinition(
    name=TASK_DEFINITION,
    title="AI 供应商连接自动检测",
    owner="core.ai",
    group="ai-connection",
    handler=handle_connection_health,
    timeout_seconds=30,
    max_attempts=1,
    concurrency=2,
    failure_notifications=False,
)


def _time_bucket(now: datetime | None = None) -> str:
    current = now or china_now()
    minute = current.minute - current.minute % INTERVAL_MINUTES
    return current.replace(minute=minute, second=0, microsecond=0).strftime(
        "%Y%m%d%H%M",
    )


async def enqueue_enabled_connection_checks(now: datetime | None = None) -> int:
    """为每个启用连接投递一次检测，同一五分钟窗口保持幂等。"""
    from services.task.queue_worker import submit_task

    async with async_session_factory() as db:
        connection_ids = (await db.execute(select(
            AgentScopeConnectionPoolModel.id,
        ).where(
            AgentScopeConnectionPoolModel.status == 1,
        ).order_by(AgentScopeConnectionPoolModel.id))).scalars().all()
    bucket = _time_bucket(now)
    for connection_id in connection_ids:
        await submit_task(
            TASK_DEFINITION,
            {"connection_id": int(connection_id)},
            description=f"AI 供应商连接自动检测 #{connection_id}",
            idempotency_key=f"connection:{connection_id}:{bucket}",
        )
    return len(connection_ids)


async def register_ai_connection_health_schedule() -> None:
    """先登记任务定义，再注册只负责入队的五分钟调度。"""
    task_registry.register(TASK_DECLARATION)
    if task_manager.scheduler.get_job(SCHEDULE_NAME):
        return
    task_manager.register_task(
        SCHEDULE_NAME,
        enqueue_enabled_connection_checks,
        "interval",
        job_kwargs={
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": 60,
        },
        first_run_delay=20,
        minutes=INTERVAL_MINUTES,
        description="AI 供应商连接自动检测",
        task_type="ai",
        emit_after_run=False,
    )
