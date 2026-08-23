# -*- coding: utf-8 -*-
"""
天气定时任务 Worker（对标 core/task/notice_worker.py 的定位）

任务清单:
- weather_pull:           interval 任务，按配置频率全量拉取产区天气
                          （max_instances=1 + coalesce + misfire 宽限，防堆积；
                          注册/重调后 60 秒内先跑首轮，不空等一个完整周期）
- weather_daily_finalize: 每日 23:55 cron 任务，定格当日日均温
- weather_clean:          每日 3:30 cron 任务，硬删过保留期的逐日历史/过期预警
                          （错开 3:00 系统日志清理与 23:55 日终定格）

系统日志策略（对齐 ZJMF：业务事件全量记载，负载靠可配置清理治理）:
- 每轮拉取/定格/清理均无条件写 1 条 hy_system_log 汇总（log_type=weather_task）
- 单产区失败明细留在 hy_weather_data.error_msg，不逐条重复入系统日志
- active_log(db=None) 自建自闭会话且写失败静默，不影响任务本体

注册策略:
- 启动时由 lifespan 调用 register_weather_tasks()，以 hy_configuration
  为唯一事实源（weather_enabled=1 才注册拉取任务）
- 管理员保存设置后调用 apply_weather_schedule() 免重启生效
  （开关切换=增删任务，频率变化=reschedule_task）
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete

from core.db.base import async_session_factory
from core.log.active_log import active_log
from services.task.task_manager import task_manager

logger = logging.getLogger(__name__)

# 任务标识
PULL_TASK_NAME = "weather_pull"
FINALIZE_TASK_NAME = "weather_daily_finalize"
CLEAN_TASK_NAME = "weather_clean"
ALERT_NOTIFY_TASK_NAME = "weather_alert_notify"

# 作业级参数: 单实例 + 合并错过的执行 + 60 秒错过宽限（防拉取堆积）
PULL_JOB_KWARGS = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 60}
# 拉取任务首跑延迟（秒）: interval 触发器默认首跑要等一个完整周期，
# 服务重启/保存设置后会重置计时，导致用户长时间看不到自动更新；
# 统一在注册/重调后 60 秒先跑一轮（pull_all 有网格去重，成本可控）
PULL_FIRST_RUN_DELAY = 60


async def weather_pull_job():
    """全量拉取任务体（每轮全量记载系统日志，失败明细见产区快照 error_msg）"""
    from core.weather_service import weather_service
    try:
        stats = await weather_service.pull_all()
        await active_log(
            f"天气全量拉取完成: 共{stats['total']}个产区, 成功{stats['success']}, "
            f"失败{stats['failed']}, 跳过{stats['skipped']}",
            log_type="weather_task",
        )
    except Exception as e:
        logger.error(f"[天气Worker] 全量拉取任务异常: {e}")
        await active_log(f"天气全量拉取任务异常: {e}", log_type="weather_task")
        raise


async def weather_daily_finalize_job():
    """日终定格任务体（每日 1 条系统日志）"""
    from core.weather_gdd import finalize_daily
    try:
        count = await finalize_daily()
        await active_log(f"日终定格完成: {count} 个产区日均温", log_type="weather_task")
    except Exception as e:
        logger.error(f"[天气Worker] 日终定格任务异常: {e}")
        await active_log(f"天气日终定格任务异常: {e}", log_type="weather_task")
        raise


async def weather_clean_job():
    """
    天气历史数据清理任务体（硬删除，照抄 task_manager._clean_old_logs 范式）

    清理边界:
    - hy_weather_daily: date < 今天-N天（积温数据基础，保留期下限 365 天）
    - hy_weather_alert: end_time < 现在-N天（仅清已失效预警，生效中永不误删）
    - 快照表/绑定表不清理（快照原地覆盖不增长，绑定属配置数据）
    """
    from core.db.weather import WeatherDaily, WeatherAlert
    from core.weather_service import weather_service
    try:
        async with async_session_factory() as db:
            settings = await weather_service.get_settings(db)
            d1 = settings["daily_retention_days"]
            d2 = settings["alert_retention_days"]
            r1 = await db.execute(delete(WeatherDaily).where(
                WeatherDaily.date < date.today() - timedelta(days=d1)
            ))
            r2 = await db.execute(delete(WeatherAlert).where(
                WeatherAlert.end_time < datetime.now() - timedelta(days=d2)
            ))
            await db.commit()
        n1, n2 = r1.rowcount or 0, r2.rowcount or 0
        logger.info(f"[天气Worker] 数据清理完成: 逐日历史{n1}条, 过期预警{n2}条")
        await active_log(
            f"天气数据清理完成: 逐日历史{n1}条, 过期预警{n2}条（保留{d1}/{d2}天）",
            log_type="weather_task",
        )
    except Exception as e:
        logger.error(f"[天气Worker] 数据清理任务异常: {e}")
        await active_log(f"天气数据清理任务异常: {e}", log_type="weather_task")
        raise


async def weather_alert_notify_job():
    """气象预警通知补推任务（每小时兜底，防立即触发漏推）

    扫描 notified=0 且生效中（end_time 未过期）的预警，补推农户+管理员双动作通知，
    推送成功置 notified=1 去重。独立于 weather_enabled 拉取开关。
    """
    from sqlalchemy import select
    from core.db.weather import WeatherAlert
    from core.weather_service import weather_service
    try:
        async with async_session_factory() as db:
            pending = (await db.execute(
                select(WeatherAlert).where(
                    WeatherAlert.notified == 0,
                    WeatherAlert.end_time >= datetime.now(),
                )
            )).scalars().all()
            count = 0
            for alert in pending:
                # ORM 字段转 dict 供 _dispatch_alert 使用
                alert_fields = {
                    "title": alert.title or "",
                    "level": alert.level or "",
                    "text": alert.text or "",
                    "start_time": alert.start_time or "",
                    "end_time": alert.end_time or "",
                }
                try:
                    await weather_service._dispatch_alert(
                        db, alert.area_id, alert_fields, alert_orm=alert)
                    count += 1
                except Exception as e:
                    logger.warning(f"[天气Worker] 预警 {alert.id} 补推失败: {e}")
            await db.commit()
        if count:
            await active_log(
                f"气象预警补推完成: {count} 条", log_type="weather_task")
            logger.info(f"[天气Worker] 气象预警补推完成: {count} 条")
    except Exception as e:
        logger.error(f"[天气Worker] 气象预警补推任务异常: {e}")
        await active_log(f"气象预警补推任务异常: {e}", log_type="weather_task")
        raise


async def _enqueue_weather_pull():
    """周期任务入队: 天气全量拉取由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("weather_pull", {}, description="天气全量拉取")


async def _enqueue_weather_daily_finalize():
    """周期任务入队: 天气日终定格由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("weather_daily_finalize", {}, description="天气日终定格")


async def _enqueue_weather_clean():
    """周期任务入队: 天气历史数据清理由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("weather_clean", {}, description="天气历史数据清理")


async def _enqueue_weather_alert_notify():
    """周期任务入队: 气象预警通知补推由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("weather_alert_notify", {}, description="气象预警通知补推")


async def register_weather_tasks():
    """
    启动时注册天气定时任务（lifespan 调用，调度器已 start）

    weather_enabled=0 时不注册拉取任务；日终定格与数据清理任务始终注册
    （历史数据的日均温定格与保留期清理不依赖拉取开关）。
    """
    from core.weather_service import weather_service
    async with async_session_factory() as db:
        settings = await weather_service.get_settings(db)

    # 日终定格任务（每日 23:55）
    if not task_manager.scheduler.get_job(FINALIZE_TASK_NAME):
        task_manager.register_task(
            FINALIZE_TASK_NAME, _enqueue_weather_daily_finalize,
            "cron", hour=23, minute=55,
            description="天气日终定格", task_type="weather",
            emit_after_run=False,
        )

    # 历史数据清理任务（每日 3:30，错开 3:00 系统日志清理与 23:55 日终定格）
    if not task_manager.scheduler.get_job(CLEAN_TASK_NAME):
        task_manager.register_task(
            CLEAN_TASK_NAME, _enqueue_weather_clean,
            "cron", hour=3, minute=30,
            description="天气历史数据清理", task_type="weather",
            emit_after_run=False,
        )

    if settings["enabled"]:
        if not task_manager.scheduler.get_job(PULL_TASK_NAME):
            task_manager.register_task(
                PULL_TASK_NAME, _enqueue_weather_pull,
                "interval", job_kwargs=PULL_JOB_KWARGS,
                first_run_delay=PULL_FIRST_RUN_DELAY,
                minutes=settings["interval_minutes"],
                description="天气全量拉取", task_type="weather",
                emit_after_run=False,
            )
        logger.info(f"[天气Worker] 拉取任务已注册（每 {settings['interval_minutes']} 分钟）")
    else:
        logger.info("[天气Worker] 天气拉取总开关未开启，跳过拉取任务注册")

    # 气象预警通知补推任务（每小时，独立于 weather_enabled 拉取开关）
    if not task_manager.scheduler.get_job(ALERT_NOTIFY_TASK_NAME):
        task_manager.register_task(
            ALERT_NOTIFY_TASK_NAME, _enqueue_weather_alert_notify,
            "interval",
            job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 300},
            first_run_delay=120, minutes=60,
            description="气象预警通知补推", task_type="weather",
            emit_after_run=False,
        )


async def apply_weather_schedule():
    """
    管理员保存设置后即时应用调度（免重启生效）

    - 开关开启: 任务不存在则注册，存在则按新频率 reschedule
    - 开关关闭: 移除拉取任务
    """
    from core.weather_service import weather_service
    async with async_session_factory() as db:
        settings = await weather_service.get_settings(db)

    if not settings["enabled"]:
        task_manager.remove_task(PULL_TASK_NAME)
        logger.info("[天气Worker] 总开关关闭，已移除拉取任务")
        await active_log("总开关关闭，已移除天气拉取任务", log_type="weather_task")
        return

    if task_manager.scheduler.get_job(PULL_TASK_NAME):
        task_manager.reschedule_task(
            PULL_TASK_NAME, "interval",
            first_run_delay=PULL_FIRST_RUN_DELAY,
            minutes=settings["interval_minutes"],
        )
        await active_log(
            f"天气拉取调度已更新: 每{settings['interval_minutes']}分钟",
            log_type="weather_task",
        )
    else:
        task_manager.register_task(
            PULL_TASK_NAME, _enqueue_weather_pull,
            "interval", job_kwargs=PULL_JOB_KWARGS,
            first_run_delay=PULL_FIRST_RUN_DELAY,
            minutes=settings["interval_minutes"],
            description="天气全量拉取", task_type="weather",
            emit_after_run=False,
        )
        await active_log(
            f"天气拉取任务已注册: 每{settings['interval_minutes']}分钟",
            log_type="weather_task",
        )
    logger.info(f"[天气Worker] 调度已应用（每 {settings['interval_minutes']} 分钟）")
