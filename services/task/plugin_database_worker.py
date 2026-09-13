"""插件数据库体检的周期投递器。"""

import logging

from core.config_service import get_config
from services.task.task_manager import task_manager

logger = logging.getLogger(__name__)

SCHEDULE_NAME = "plugin_database_scan_schedule"
TASK_NAME = "plugin_database_scan"
DEFAULT_INTERVAL_HOURS = 6
ALLOWED_INTERVALS = (1, 6, 12, 24)


async def _enabled_and_interval() -> tuple[bool, int]:
    enabled = await get_config("plugin_database_scan_enabled")
    interval = await get_config("plugin_database_scan_interval_hours")
    is_enabled = str(enabled if enabled is not None else "1").lower() not in {"0", "false", "off", "no"}
    try:
        hours = int(interval or DEFAULT_INTERVAL_HOURS)
    except (TypeError, ValueError):
        hours = DEFAULT_INTERVAL_HOURS
    return is_enabled, hours if hours in ALLOWED_INTERVALS else DEFAULT_INTERVAL_HOURS


async def enqueue_plugin_database_scan() -> int:
    """只投递扫描队列任务，活动任务复用、同一小时重复投递去重。"""
    from services.task.queue_worker import submit_task
    active = await active_plugin_database_scan_task()
    if active:
        return int(active)
    from core.time_utils import china_now
    bucket = china_now().strftime("%Y%m%d%H")
    return await submit_task(
        TASK_NAME, {}, description="插件数据库体检",
        idempotency_key=f"plugin-database-scan:{bucket}",
    )


async def active_plugin_database_scan_task() -> int | None:
    """读取队列中尚未完成的扫描任务，供接口返回复用标记。"""
    from sqlalchemy import select
    from core.db.base import async_session_factory
    from core.db.task_queue import TaskQueue

    async with async_session_factory() as db:
        active = (await db.execute(select(TaskQueue.id).where(
            TaskQueue.definition == TASK_NAME,
            TaskQueue.status.in_(("Wait", "Exec")),
        ).order_by(TaskQueue.id.desc()).limit(1))).scalar_one_or_none()
    return int(active) if active else None


async def register_plugin_database_schedule() -> None:
    enabled, hours = await _enabled_and_interval()
    if not enabled:
        task_manager.remove_task(SCHEDULE_NAME)
        return
    if task_manager.scheduler.get_job(SCHEDULE_NAME):
        return
    task_manager.register_task(
        SCHEDULE_NAME, enqueue_plugin_database_scan, "interval",
        hours=hours, description="插件数据库体检调度", task_type="system",
        emit_after_run=False,
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 3600},
    )
    logger.info("[插件数据库体检] 调度已注册（每 %s 小时）", hours)


async def apply_plugin_database_schedule() -> None:
    enabled, hours = await _enabled_and_interval()
    if not enabled:
        task_manager.remove_task(SCHEDULE_NAME)
        return
    if task_manager.scheduler.get_job(SCHEDULE_NAME):
        task_manager.reschedule_task(SCHEDULE_NAME, "interval", hours=hours)
    else:
        await register_plugin_database_schedule()
