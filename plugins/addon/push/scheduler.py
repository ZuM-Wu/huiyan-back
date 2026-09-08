# -*- coding: utf-8 -*-
"""
推送调度器（任务体系调度）

基于 TaskManager 定时任务，每分钟轮询到期的推送任务并投递到任务队列执行。
支持周期类型：onetime（一次性）/ day（每日）/ week（每周）/ month（每月）

调度逻辑：
1. 查询所有 status=Wait 且在有效期内的任务
2. 判断当前时间是否匹配发送时间（时:分）
3. 对于周期性任务，额外判断 week_day / month_day
4. 符合条件的任务经 submit_task 投递（push_execute），由 queue_worker 分发执行
"""
import logging
from datetime import datetime, time
from core.time_utils import china_now

from sqlalchemy import select

from core.db.base import async_session_factory
from services.task.queue_worker import submit_task
from services.task.task_manager import task_manager
from plugins.addon.push.models import PushCenterTask

logger = logging.getLogger(__name__)

# 调度任务注册名（与 TaskManager 作业 id 一致）
SCHEDULER_TASK_NAME = "push_scheduler"


async def register_push_scheduler(interval_seconds: int = 60) -> None:
    """注册推送调度定时任务（幂等：已注册则跳过）"""
    if task_manager.scheduler.get_job(SCHEDULER_TASK_NAME):
        logger.warning("[推送调度器] 调度任务已注册")
        return
    task_manager.register_task(
        SCHEDULER_TASK_NAME, _check_and_dispatch,
        "interval", seconds=interval_seconds,
        first_run_delay=60,
        description="推送任务调度", task_type="push_task",
    )
    logger.info(f"[推送调度器] 调度任务已注册，轮询间隔 {interval_seconds}s")


async def remove_push_scheduler() -> None:
    """移除推送调度定时任务"""
    task_manager.remove_task(SCHEDULER_TASK_NAME)
    logger.info("[推送调度器] 调度任务已移除")


async def register_waiting_onetime_tasks() -> None:
    """将已有的一次性等待任务补登记到系统任务队列。"""
    async with async_session_factory() as db:
        result = await db.execute(select(PushCenterTask).where(PushCenterTask.status == "Wait"))
        tasks = [task for task in result.scalars().all()
                 if (task.schedule_rule or {}).get("cycle") == "onetime"]

    for task in tasks:
        await schedule_onetime_task(task)


async def schedule_onetime_task(task: PushCenterTask) -> int:
    """登记一次性推送任务，并由队列在计划时间到达后执行。"""
    if task.status != "Wait" or _schedule(task).get("cycle") != "onetime":
        return 0
    return await submit_task(
        "push_execute",
        {"task_id": task.id},
        description=task.title or "推送任务",
        run_at=get_onetime_run_at(task),
    )


def get_onetime_run_at(task: PushCenterTask) -> datetime:
    """将一次性任务的日期与发送时刻合成为队列计划执行时间。"""
    schedule = _schedule(task)
    reference = _as_datetime(schedule.get("start_time")) or task.create_time or china_now()
    task_time = _parse_task_time(schedule.get("time_hour_min"))
    run_at = datetime.combine(reference.date(), task_time)
    start_time = _as_datetime(schedule.get("start_time"))
    if start_time and run_at < start_time:
        return start_time
    return run_at


async def _check_and_dispatch() -> None:
    """检查并分发到期任务"""
    now = china_now()
    current_hour_min = now.strftime("%H:%M")

    async with async_session_factory() as db:
        # 查询所有等待执行的任务
        result = await db.execute(
            select(PushCenterTask).where(
                PushCenterTask.status == "Wait",
            )
        )
        tasks = result.scalars().all()

    for task in tasks:
        try:
            if await _should_execute(task, now, current_hour_min):
                logger.info(f"[推送调度器] 触发任务: id={task.id}, title={task.title}")
                # 投递到任务队列异步执行，不阻塞调度轮询
                await submit_task(
                    "push_execute", {"task_id": task.id},
                    description=task.title or "推送任务",
                )
        except Exception as e:
            logger.error(f"[推送调度器] 任务判断异常: id={task.id}, {e}")


async def _should_execute(task: PushCenterTask, now: datetime, current_hour_min: str) -> bool:
    """判断任务是否应该在当前时刻执行"""
    # 检查有效期
    schedule = _schedule(task)
    start_time = _as_datetime(schedule.get("start_time"))
    end_time = _as_datetime(schedule.get("end_time"))
    if start_time and now < start_time:
        return False
    if end_time and now > end_time:
        # 超过有效期，标记为过期（短操作直接执行）
        await _mark_expired(task.id)
        return False

    # 到达计划时刻即可执行，避免轮询错过精确分钟后任务永久滞留。
    scheduled_at = datetime.combine(now.date(), _parse_task_time(schedule.get("time_hour_min")))
    if now < scheduled_at:
        return False

    # 检查周期条件
    cycle = schedule.get("cycle") or "onetime"

    if cycle == "onetime":
        # 一次性任务：未执行过 + 时间匹配
        return task.last_exec_time is None

    elif cycle == "day":
        # 每日任务：今天未执行过
        if task.last_exec_time:
            return task.last_exec_time.date() < now.date()
        return True

    elif cycle == "week":
        # 每周任务：当前星期几匹配 + 本周未执行
        # Python weekday: 0=Mon, isoweekday: 1=Mon ~ 7=Sun
        target_day = schedule.get("week_day") or 1
        if now.isoweekday() != target_day:
            return False
        if task.last_exec_time:
            return task.last_exec_time.date() < now.date()
        return True

    elif cycle == "month":
        # 每月任务：当前日期匹配 + 本月未执行
        target_day = schedule.get("month_day") or 1
        if now.day != target_day:
            return False
        if task.last_exec_time:
            return task.last_exec_time.date() < now.date()
        return True

    return False


def _parse_task_time(value: str | None) -> time:
    """解析任务发送时刻，兼容历史异常配置并回退到默认值。"""
    try:
        return datetime.strptime(value or "09:00", "%H:%M").time()
    except ValueError:
        return time(hour=9)


async def _mark_expired(task_id: int) -> None:
    """将过期任务标记为 Expired"""
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(PushCenterTask).where(PushCenterTask.id == task_id))
            task = result.scalar_one_or_none()
            if task and task.status == "Wait":
                task.status = "Expired"
                await db.commit()
                logger.info(f"[推送调度器] 任务已过期: id={task_id}")
    except Exception as e:
        logger.error(f"[推送调度器] 标记过期失败: id={task_id}, {e}")


def _as_datetime(value):
    """将 JSON 中的 ISO 时间安全转换为 datetime。"""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _schedule(task) -> dict:
    """读取新任务规则，同时兼容旧 PushTask 的字段。"""
    if hasattr(task, "schedule_rule"):
        return task.schedule_rule or {}
    return {
        "cycle": getattr(task, "send_cycle", "onetime") or "onetime",
        "time_hour_min": getattr(task, "time_hour_min", "09:00") or "09:00",
        "week_day": getattr(task, "week_day", 1) or 1,
        "month_day": getattr(task, "month_day", 1) or 1,
        "start_time": getattr(task, "push_start_time", None),
        "end_time": getattr(task, "push_end_time", None),
    }
