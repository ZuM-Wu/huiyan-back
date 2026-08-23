# -*- coding: utf-8 -*-
"""
周期任务队列 handler（核心，非插件）

周期调度器只负责发现并 submit_task 投递，实际业务执行体在队列 Worker
中分发。每个 handler 包装原 *_job / _clean_* 函数，执行后写 hy_task_log
并统一记录成功/失败；失败继续上抛，让队列按策略重试或转 Dead
并按 max_retry 自动重试。
"""
async def _run_clean_repeat_cache(task_data: dict):
    """执行清理过期防重复缓存"""
    from services.task.task_manager import _clean_repeat_cache
    await _clean_repeat_cache()


async def _run_clean_old_logs(task_data: dict):
    """执行清理过期系统日志"""
    from services.task.task_manager import _clean_old_logs
    await _clean_old_logs()


async def _run_clean_task_logs(task_data: dict):
    """执行清理过期任务日志"""
    from services.task.task_manager import _clean_task_logs
    await _clean_task_logs()


async def _run_sweep_expired_cache(task_data: dict):
    """执行清扫过期缓存"""
    from services.task.task_manager import _sweep_expired_cache
    await _sweep_expired_cache()


async def _run_weather_pull(task_data: dict):
    """执行天气全量拉取"""
    from services.task.weather_worker import weather_pull_job
    await weather_pull_job()


async def _run_weather_daily_finalize(task_data: dict):
    """执行天气日终定格"""
    from services.task.weather_worker import weather_daily_finalize_job
    await weather_daily_finalize_job()


async def _run_weather_clean(task_data: dict):
    """执行天气历史数据清理"""
    from services.task.weather_worker import weather_clean_job
    await weather_clean_job()


async def _run_weather_alert_notify(task_data: dict):
    """执行气象预警通知补推"""
    from services.task.weather_worker import weather_alert_notify_job
    await weather_alert_notify_job()


async def _handle_clean_repeat_cache(task_data: dict):
    await _run_clean_repeat_cache(task_data)


async def _handle_clean_old_logs(task_data: dict):
    """队列 handler: 清理过期系统日志"""
    await _run_clean_old_logs(task_data)


async def _handle_clean_task_logs(task_data: dict):
    """队列 handler: 清理过期任务日志"""
    await _run_clean_task_logs(task_data)


async def _handle_sweep_expired_cache(task_data: dict):
    """队列 handler: 清扫过期缓存"""
    await _run_sweep_expired_cache(task_data)


async def _handle_weather_pull(task_data: dict):
    """队列 handler: 天气全量拉取"""
    await _run_weather_pull(task_data)


async def _handle_weather_daily_finalize(task_data: dict):
    """队列 handler: 天气日终定格"""
    await _run_weather_daily_finalize(task_data)


async def _handle_weather_clean(task_data: dict):
    """队列 handler: 天气历史数据清理"""
    await _run_weather_clean(task_data)


async def _handle_weather_alert_notify(task_data: dict):
    """队列 handler: 气象预警通知补推"""
    await _run_weather_alert_notify(task_data)


# 队列类型 -> handler 注册表
_PERIODIC_HANDLERS = [
    ("clean_repeat_cache", "清理过期防重复缓存", "system", _handle_clean_repeat_cache),
    ("clean_old_logs", "清理过期系统日志", "system", _handle_clean_old_logs),
    ("clean_task_logs", "清理过期任务日志", "system", _handle_clean_task_logs),
    ("sweep_expired_cache", "清扫过期缓存文件", "system", _handle_sweep_expired_cache),
    ("weather_pull", "天气全量拉取", "weather", _handle_weather_pull),
    ("weather_daily_finalize", "天气日终定格", "weather", _handle_weather_daily_finalize),
    ("weather_clean", "天气历史数据清理", "weather", _handle_weather_clean),
    ("weather_alert_notify", "气象预警通知补推", "weather", _handle_weather_alert_notify),
]


def periodic_handlers() -> list[tuple]:
    """返回周期任务定义所需的稳定元数据。"""
    return list(_PERIODIC_HANDLERS)
