"""
任务队列
慧眼护农 3.4.1 任务子系统

使用 APScheduler 实现定时任务调度

周期任务执行结果统一写执行日志；最终失败发布可靠 task.failed 事件。
"""

import inspect
import logging
import time
from datetime import timedelta
from core.time_utils import CHINA_TIMEZONE, china_now, china_now_aware

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.db.base import async_session_factory

logger = logging.getLogger(__name__)


class TaskManager:
    """
    任务管理器
    """

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=CHINA_TIMEZONE)
        self._tasks = {}

    def start(self):
        """启动调度器"""
        self._register_builtin_tasks()
        self.scheduler.start()
        logger.info("[TaskManager] 任务调度器已启动")

    def shutdown(self):
        """关闭调度器"""
        self.scheduler.shutdown(wait=False)
        logger.info("[TaskManager] 任务调度器已关闭")

    def register_task(self, name: str, func, trigger, job_kwargs: dict | None = None,
                      first_run_delay: int | None = None,
                      description: str = "", task_type: str = "system",
                      emit_after_run: bool = True,
                      **kwargs):
        """
        注册定时任务

        参数:
            name:   任务名称（英文标识，如 weather_pull）
            func:   执行函数
            trigger: 'interval' 或 'cron'
            job_kwargs: 可选，透传给 add_job 的作业级参数
                （如 max_instances/coalesce/misfire_grace_time，防任务堆积）
            first_run_delay: 可选，首次运行延迟（秒）。interval 任务默认
                首跑要等一个完整周期，传入后可在注册后短时间内先跑一轮
            description: 可选，任务中文描述（如"天气全量拉取"）
            task_type: 可选，任务类型 system/weather/notice/plugin
            emit_after_run: 可选，False 表示该任务只负责投递队列任务，
                实际执行结果由队列 handler 记录，避免重复写任务日志/触发钩子
            **kwargs: trigger 参数
        """
        job_kwargs = dict(job_kwargs or {})
        if trigger == "interval":
            t = IntervalTrigger(timezone=CHINA_TIMEZONE, **kwargs)
        elif trigger == "cron":
            t = CronTrigger(timezone=CHINA_TIMEZONE, **kwargs)
        else:
            raise ValueError(f"不支持的触发器类型: {trigger}")

        # 指定首次运行时间，避免 interval 任务注册后空等一个周期
        if first_run_delay is not None:
            job_kwargs["next_run_time"] = (
                china_now_aware() + timedelta(seconds=first_run_delay)
            )

        # 包装任务函数，执行完毕后统一记录结果并发布失败事件。
        wrapped = self._wrap_task_func(
            name, func, description, task_type, emit_after_run
        )

        job = self.scheduler.add_job(wrapped, t, id=name, name=name, **job_kwargs)
        self._tasks[name] = job
        logger.info(f"[TaskManager] 已注册任务: {name} ({description or name})")

    @staticmethod
    def _wrap_task_func(name: str, func, description: str, task_type: str,
                        emit_after_run: bool = True):
        """
        包装任务函数，在执行完毕后统一记录结果

        - 成功/失败都记录（finally 块）
        - 结果记录失败不影响任务本体（内部安全调用）
        - 异步函数保持异步，同步函数包装为异步以统一调用路径
        """
        is_async = inspect.iscoroutinefunction(func)

        if is_async:
            async def _wrapped(*args, **kwargs):
                start = time.time()
                _status = "success"
                _error = ""
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    _status = "failed"
                    _error = str(e)
                    raise
                finally:
                    if emit_after_run:
                        _dur = int((time.time() - start) * 1000)
                        await _emit_after_task_run(
                            name, _status, _error, _dur, description, task_type
                        )
            return _wrapped
        else:
            async def _wrapped_sync(*args, **kwargs):
                start = time.time()
                _status = "success"
                _error = ""
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    _status = "failed"
                    _error = str(e)
                    raise
                finally:
                    if emit_after_run:
                        _dur = int((time.time() - start) * 1000)
                        await _emit_after_task_run(
                            name, _status, _error, _dur, description, task_type
                        )
            return _wrapped_sync

    def reschedule_task(self, name: str, trigger: str, first_run_delay: int | None = None,
                        **kwargs):
        """
        重新调整已注册任务的触发器（免重启生效）

        供管理员在页面上修改定时任务频率后即时应用，
        例如天气拉取频率从 15 分钟改为 30 分钟。

        参数:
            name:   任务名称（需已通过 register_task 注册）
            trigger: 'interval' 或 'cron'
            first_run_delay: 可选，重调后首次运行延迟（秒），
                不传则从重调时刻起等一个完整周期
            **kwargs: trigger 参数（如 minutes=30）
        返回:
            True=调整成功；False=任务不存在或参数非法
        """
        if trigger == "interval":
            t = IntervalTrigger(timezone=CHINA_TIMEZONE, **kwargs)
        elif trigger == "cron":
            t = CronTrigger(timezone=CHINA_TIMEZONE, **kwargs)
        else:
            raise ValueError(f"不支持的触发器类型: {trigger}")

        job = self.scheduler.get_job(name)
        if not job:
            logger.warning(f"[TaskManager] 重调失败，任务不存在: {name}")
            return False
        self.scheduler.reschedule_job(name, trigger=t)
        # 重调会重置计时，按需把首次运行提前到短延迟后（免空等一个周期）
        if first_run_delay is not None:
            self.scheduler.modify_job(
                name, next_run_time=china_now_aware() + timedelta(seconds=first_run_delay)
            )
        logger.info(f"[TaskManager] 已重调任务触发器: {name}")
        return True

    def remove_task(self, name: str):
        """移除已注册的定时任务（任务不存在时静默忽略）"""
        job = self.scheduler.get_job(name)
        if job:
            self.scheduler.remove_job(name)
            self._tasks.pop(name, None)
            logger.info(f"[TaskManager] 已移除任务: {name}")

    async def run_task_manually(self, name: str):
        """
        手动触发指定任务执行一次（不影响定时调度）

        供管理员在页面上点击"重试"时调用，复用包装函数统一记录结果。

        参数:
            name: 任务名称（需已通过 register_task 注册）
        Raises:
            ValueError: 任务不存在
        """
        job = self.scheduler.get_job(name)
        if not job:
            raise ValueError(f"任务不存在: {name}")
        func = job.func
        if inspect.iscoroutinefunction(func):
            await func()
        else:
            func()
        logger.info(f"[TaskManager] 手动执行任务: {name}")

    def _register_builtin_tasks(self):
        """注册内置定时任务"""
        # 每小时清理过期的防重复缓存
        self.register_task(
            "clean_repeat_cache",
            _enqueue_clean_repeat_cache,
            "interval",
            hours=1,
            description="清理过期防重复缓存",
            task_type="system",
            emit_after_run=False,
        )

        # 每天凌晨 3:00 清理过期日志（开关+保留天数可配置，默认保留90天）
        self.register_task(
            "clean_old_logs",
            _enqueue_clean_old_logs,
            "cron",
            hour=3,
            minute=0,
            description="清理过期系统日志",
            task_type="system",
            emit_after_run=False,
        )

        # 每天凌晨 4:00 清理过期任务执行日志（默认保留 30 天）
        self.register_task(
            "clean_task_logs",
            _enqueue_clean_task_logs,
            "cron",
            hour=4,
            minute=0,
            description="清理过期任务日志",
            task_type="system",
            emit_after_run=False,
        )

        # 每天凌晨 5:00 清扫过期缓存文件（配合文件缓存的惰性删除兼做定期全量清理）
        self.register_task(
            "sweep_expired_cache",
            _enqueue_sweep_expired_cache,
            "cron",
            hour=5,
            minute=0,
            description="清扫过期缓存文件",
            task_type="system",
            emit_after_run=False,
        )


async def _emit_after_task_run(task_name: str, status: str, error_msg: str,
                             duration_ms: int, task_desc: str,
                             task_type: str):
    """
    周期任务执行完毕后的统一处理（安全调用，失败不阻断调度器）。

    参数:
        task_name:   任务名称（英文标识）
        status:      success / failed
        error_msg:   错误信息（成功时为空）
        duration_ms: 执行耗时（毫秒）
        task_desc:   任务中文描述
        task_type:   任务类型 system/weather/notice/plugin
    """
    # 1. 核心处理：记录日志 + 失败时写系统日志
    try:
        from services.task.task_monitor import task_monitor
        await task_monitor.record_task_result(
            task_name, status, error_msg, duration_ms, task_desc, task_type
        )
    except Exception as e:
        logger.warning(f"[TaskManager] 任务日志记录失败: {e}")

    if status != "failed":
        return
    try:
        from core.events import event_bus
        async with async_session_factory() as db:
            await event_bus.publish_durable("task.failed", {
                "task_id": 0,
                "owner": task_type or "system",
                "definition": task_name,
                "attempt": 1,
                "max_attempts": 1,
                "error_msg": error_msg,
                "correlation_id": "",
                "event_id": None,
            }, db)
            await db.commit()
    except Exception as e:
        logger.warning(f"[TaskManager] task.failed 可靠事件发布失败: {e}")


async def _clean_repeat_cache():
    """清理过期的防重复提交缓存"""
    from core.auth.middleware_chain import _repeat_cache
    import time
    now = time.time()
    expired = [k for k, v in _repeat_cache.items() if v.get("expire", 0) < now]
    for k in expired:
        del _repeat_cache[k]
    if expired:
        logger.debug(f"[Task] 清理了 {len(expired)} 条过期防重复缓存")


async def _clean_old_logs():
    """
    按配置清理过期系统日志（对齐 ZJMF cron_system_log_delete 方案）

    - system_log_delete_switch=0 时跳过清理
    - system_log_delete_days 非法/缺失时回落 90 天
    - 硬删除后写 1 条系统日志记录清理结果（每日 1 条）
    """
    from datetime import timedelta
    from sqlalchemy import delete
    from core.db.system_log import SystemLog
    from core.config_manager import ConfigManager
    from core.log.active_log import active_log

    async with async_session_factory() as db:
        cm = ConfigManager()
        if str(await cm.get("system_log_delete_switch", db) or "1") == "0":
            logger.info("[Task] 系统日志自动清理开关已关闭，跳过")
            return
        try:
            days = int(await cm.get("system_log_delete_days", db) or 90)
        except (TypeError, ValueError):
            days = 90
        if days <= 0:
            days = 90

        cutoff = china_now() - timedelta(days=days)
        result = await db.execute(
            delete(SystemLog).where(SystemLog.create_time < cutoff)
        )
        await db.commit()
        count = result.rowcount or 0

    await active_log(f"系统日志自动清理完成: 删除{count}条（保留{days}天）", log_type="system")
    if count:
        logger.info(f"[Task] 清理了 {count} 条过期日志")


async def _clean_task_logs():
    """清理过期的任务执行日志（保留期由配置决定，默认 30 天）"""
    from services.task.task_monitor import task_monitor
    from core.config_manager import ConfigManager

    async with async_session_factory() as db:
        cm = ConfigManager()
        retention_str = await cm.get("task_log_retention_days", db)
    retention_days = int(retention_str or "30")

    count = await task_monitor.clean_expired_logs(retention_days)
    if count:
        logger.info(f"[Task] 清理了 {count} 条过期任务日志")


async def _sweep_expired_cache():
    """清扫过期的文件缓存（补充惰性删除，避免长期不读的过期文件堆积）"""
    from core.cache.cache_manager import cache_manager
    removed = cache_manager.sweep_expired()
    if removed:
        logger.info(f"[Task] 清扫了 {removed} 个过期缓存文件")


async def _enqueue_clean_repeat_cache():
    """周期任务入队: 清理过期防重复缓存由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("clean_repeat_cache", {}, description="清理过期防重复缓存")


async def _enqueue_clean_old_logs():
    """周期任务入队: 清理过期系统日志由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("clean_old_logs", {}, description="清理过期系统日志")


async def _enqueue_clean_task_logs():
    """周期任务入队: 清理过期任务日志由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("clean_task_logs", {}, description="清理过期任务日志")


async def _enqueue_sweep_expired_cache():
    """周期任务入队: 清扫过期缓存由队列 Worker 执行"""
    from services.task.queue_worker import submit_task
    await submit_task("sweep_expired_cache", {}, description="清扫过期缓存文件")


# 全局单例
task_manager = TaskManager()
