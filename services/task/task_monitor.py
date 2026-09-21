# -*- coding: utf-8 -*-
"""
任务监控服务（核心模块）

核心职责:
1. 记录任务执行日志到 hy_task_log
2. 任务失败时写系统日志（平台内告知管理员）
3. 支持手动重试失败任务
4. 支持标记已处理/已忽略
5. 定时清理过期日志，并支持管理员手动清理保留期外日志
6. 任务状态概览

本模块为系统核心，不依赖任何插件。
周期任务最终失败会发布 task.failed 可靠事件。
"""
import logging
from datetime import timedelta
from core.time_utils import china_now
from typing import Any, cast

from sqlalchemy import select, update, delete
from sqlalchemy.engine import CursorResult

from core.db.base import async_session_factory
from core.db.task_log import TaskLog
from core.log.active_log import active_log

logger = logging.getLogger(__name__)


class TaskMonitorService:
    """任务监控服务单例"""

    async def record_task_result(self, task_name: str, status: str,
                                  error_msg: str, duration_ms: int,
                                  task_desc: str = "",
                                  task_type: str = "system"):
        """
        任务执行完毕后的统一处理入口（由 task_manager 直接调用）

        1. 写入 hy_task_log
        2. 失败时写系统日志（平台内告知管理员）
        """
        now = china_now()
        start_time = now - timedelta(milliseconds=duration_ms) if duration_ms else now

        async with async_session_factory() as db:
            log = TaskLog(
                task_name=task_name,
                task_desc=task_desc or task_name,
                task_type=task_type,
                status=status,
                error_msg=error_msg if status == "failed" else "",
                duration_ms=duration_ms,
                start_time=start_time,
                end_time=now,
                create_time=now,
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = log.id

        # 失败时写系统日志（平台内告知，核心行为，不依赖插件）
        if status == "failed":
            try:
                await active_log(
                    f"任务执行失败: {task_desc or task_name} — {error_msg}",
                    log_type="task_alert",
                )
            except Exception as e:
                logger.warning(f"[TaskMonitor] 系统日志写入失败: {e}")

        logger.debug(
            f"[TaskMonitor] 任务日志已记录: {task_name} status={status} log_id={log_id}"
        )

    async def retry_task(self, log_id: int, admin_id: int,
                         admin_name: str) -> dict:
        """
        手动重试失败任务

        1. 查询原日志获取 task_name
        2. 调用 task_manager.run_task_manually 执行
        3. 更新原日志为已处理
        """
        from services.task.task_manager import task_manager

        async with async_session_factory() as db:
            result = await db.execute(
                select(TaskLog).where(TaskLog.id == log_id)
            )
            log = result.scalar_one_or_none()
            if not log:
                return {"success": False, "msg": "日志不存在"}

            task_name = log.task_name

            # 更新原日志为已处理
            log.handle_status = 1
            log.handled_by = admin_id
            log.handled_by_name = admin_name
            log.handled_time = china_now()
            log.handle_note = "手动重试"
            await db.commit()

        # 执行任务（会统一记录结果，失败时发布可靠事件）
        try:
            await task_manager.run_task_manually(task_name)
            await active_log(
                f"管理员 {admin_name} 手动重试任务: {task_name}",
                log_type="task_alert",
            )
            return {"success": True, "msg": f"任务 {task_name} 重试已执行"}
        except ValueError as e:
            return {"success": False, "msg": str(e)}
        except Exception as e:
            return {"success": False, "msg": f"重试执行异常: {e}"}

    async def mark_handled(self, log_id: int, admin_id: int,
                           admin_name: str, note: str = "") -> dict:
        """标记日志为已处理"""
        async with async_session_factory() as db:
            await db.execute(
                update(TaskLog).where(TaskLog.id == log_id).values(
                    handle_status=1,
                    handled_by=admin_id,
                    handled_by_name=admin_name,
                    handled_time=china_now(),
                    handle_note=note,
                )
            )
            await db.commit()
        return {"success": True, "msg": "已标记为已处理"}

    async def mark_ignored(self, log_id: int, admin_id: int,
                           admin_name: str, note: str = "") -> dict:
        """标记日志为已忽略"""
        async with async_session_factory() as db:
            await db.execute(
                update(TaskLog).where(TaskLog.id == log_id).values(
                    handle_status=2,
                    handled_by=admin_id,
                    handled_by_name=admin_name,
                    handled_time=china_now(),
                    handle_note=note,
                )
            )
            await db.commit()
        return {"success": True, "msg": "已标记为已忽略"}

    async def clean_expired_logs(self, retention_days: int = 30):
        """清理超过保留期的任务日志"""
        cutoff = china_now() - timedelta(days=retention_days)
        async with async_session_factory() as db:
            result = cast(CursorResult[Any], await db.execute(
                delete(TaskLog).where(TaskLog.create_time < cutoff)
            ))
            await db.commit()
            count = result.rowcount or 0
        if count:
            logger.info(f"[TaskMonitor] 清理了 {count} 条过期任务日志")
        return count

# 全局单例
task_monitor = TaskMonitorService()
