"""农业政策资讯周期投递调度。"""
from core.time_utils import china_now
from services.task.queue_worker import submit_task
from services.task.task_manager import task_manager

SCHEDULE_NAME = "policy_news.refresh_schedule"
TASK_NAME = "policy_news.refresh"
DEFAULT_INTERVAL_HOURS = 6


async def enqueue_refresh(interval_hours: int = DEFAULT_INTERVAL_HOURS) -> int:
    """将一次抓取提交到队列，使用当前周期时间桶幂等。"""
    now = china_now()
    interval_hours = max(1, min(int(interval_hours or DEFAULT_INTERVAL_HOURS), 24))
    bucket = now.replace(
        hour=(now.hour // interval_hours) * interval_hours,
        minute=0, second=0, microsecond=0,
    )
    return await submit_task(
        TASK_NAME,
        {},
        description="刷新农业政策资讯",
        idempotency_key=f"policy_news:{bucket.isoformat()}",
    )


async def register_schedule(interval_hours: int = DEFAULT_INTERVAL_HOURS) -> None:
    """幂等注册政策抓取周期任务。"""
    if task_manager.scheduler.get_job(SCHEDULE_NAME):
        return
    interval_hours = max(1, min(int(interval_hours or DEFAULT_INTERVAL_HOURS), 24))
    async def scheduled_enqueue() -> int:
        return await enqueue_refresh(interval_hours)

    task_manager.register_task(
        SCHEDULE_NAME,
        scheduled_enqueue,
        "interval",
        first_run_delay=60,
        hours=interval_hours,
        description="农业政策资讯周期投递",
        task_type="policy_news",
        emit_after_run=False,
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 300},
    )


def remove_schedule() -> None:
    """移除政策抓取周期任务。"""
    task_manager.remove_task(SCHEDULE_NAME)
