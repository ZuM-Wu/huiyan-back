"""硬件实时数据自动获取的周期调度与队列执行入口。"""

import logging

from core.log.active_log import active_log
from core.time_utils import china_now
from services.task.task_manager import task_manager

logger = logging.getLogger(__name__)

PULL_TASK_NAME = "hardware_realtime_pull"
PULL_FIRST_RUN_DELAY = 60
PULL_JOB_KWARGS = {
    "max_instances": 1,
    "coalesce": True,
    "misfire_grace_time": 60,
}


async def hardware_realtime_pull_job() -> None:
    """队列执行体：同步列表并刷新全部可用设备的最新实时快照。"""
    from core.hardware_realtime_service import pull_all_hardware_realtime

    try:
        summary = await pull_all_hardware_realtime()
        if summary.get("skipped"):
            logger.info("[硬件实时数据] %s，本轮跳过", summary.get("reason", "自动获取开关已关闭"))
            return
        await active_log(
            "硬件实时数据自动获取完成: "
            f"共{summary['total']}台, 成功{summary['success']}台, "
            f"失败{summary['failed']}台，来源同步失败{summary.get('source_failed', 0)}项",
            log_type="hardware_task",
        )
    except Exception as exc:
        logger.error("[硬件实时数据] 自动获取失败: %s", exc)
        await active_log(
            f"硬件实时数据自动获取失败: {exc}",
            log_type="hardware_task",
        )
        raise


async def _has_active_queue_task() -> bool:
    """检查同定义待执行或执行中任务，避免外部平台变慢时形成积压。"""
    from core.platform.task import query_queue

    waiting = await query_queue(
        page=1, limit=1, status="Wait", task_type=PULL_TASK_NAME
    )
    if waiting["total"]:
        return True
    executing = await query_queue(
        page=1, limit=1, status="Exec", task_type=PULL_TASK_NAME
    )
    return bool(executing["total"])


async def _enqueue_hardware_realtime_pull() -> None:
    """周期发现函数只负责把实际采集任务投递到 MySQL 队列。"""
    if await _has_active_queue_task():
        logger.info("[硬件实时数据] 已有待执行或执行中任务，本轮不重复投递")
        return
    from services.task.queue_worker import submit_task

    second_key = china_now().strftime("scheduled:%Y%m%d%H%M%S")
    await submit_task(
        PULL_TASK_NAME,
        {},
        description="硬件实时数据自动获取",
        idempotency_key=second_key,
    )


def _register_pull_task(interval_seconds: int) -> None:
    """按统一参数注册硬件实时数据周期入队任务。"""
    task_manager.register_task(
        PULL_TASK_NAME,
        _enqueue_hardware_realtime_pull,
        "interval",
        job_kwargs=PULL_JOB_KWARGS,
        first_run_delay=min(PULL_FIRST_RUN_DELAY, interval_seconds),
        seconds=interval_seconds,
        description="硬件实时数据自动获取",
        task_type="hardware",
        emit_after_run=False,
    )


async def register_hardware_realtime_schedule() -> None:
    """应用启动时按数据库设置恢复硬件自动获取调度。"""
    from core.hardware_realtime_service import get_auto_fetch_settings

    settings = await get_auto_fetch_settings()
    if not settings["enabled"]:
        logger.info("[硬件实时数据] 自动获取未开启，跳过调度注册")
        return
    if not task_manager.scheduler.get_job(PULL_TASK_NAME):
        _register_pull_task(settings["interval_seconds"])
    logger.info(
        "[硬件实时数据] 调度已注册（每 %s 秒）",
        settings["interval_seconds"],
    )


async def apply_hardware_realtime_schedule() -> None:
    """管理员保存设置后即时增删或重排调度，无需重启应用。"""
    from core.hardware_realtime_service import get_auto_fetch_settings

    settings = await get_auto_fetch_settings()
    if not settings["enabled"]:
        task_manager.remove_task(PULL_TASK_NAME)
        await active_log(
            "硬件实时数据自动获取已关闭",
            log_type="hardware_task",
        )
        return
    if task_manager.scheduler.get_job(PULL_TASK_NAME):
        task_manager.reschedule_task(
            PULL_TASK_NAME,
            "interval",
            first_run_delay=min(PULL_FIRST_RUN_DELAY, settings["interval_seconds"]),
            seconds=settings["interval_seconds"],
        )
    else:
        _register_pull_task(settings["interval_seconds"])
    await active_log(
        f"硬件实时数据调度已应用: 每{settings['interval_seconds']}秒",
        log_type="hardware_task",
    )
