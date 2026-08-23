# -*- coding: utf-8 -*-
"""基于 MySQL 的任务执行器，提供并发隔离、退避、超时与死信。"""

import asyncio
import inspect
import json
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from core.config_manager import ConfigManager
from core.db.base import async_session_factory
from core.db.event_outbox import EventOutbox
from core.db.task_log import TaskLog
from core.db.task_queue import TaskQueue
from services.task.definitions import TaskContext, TaskDefinition, task_registry

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "task_queue_enabled": 1,
    "task_queue_poll_interval": 3,
    "task_queue_batch_size": 10,
    "task_queue_clean_finish": 1,
}
_WORKER_STOP_TIMEOUT_SECONDS = 30
_INTERRUPTED_TASK_REASON = "进程中断，任务已重新入队"


async def _get_config(key: str, default: Any = None) -> Any:
    async with async_session_factory() as db:
        value = await ConfigManager().get(key, db)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def retry_delay_seconds(attempt: int, jitter_value: float | None = None) -> float:
    """计算指数退避时间，上限 15 分钟并加入正负 20% 抖动。"""
    base = min(5 * (2 ** max(attempt - 1, 0)), 900)
    jitter = random.uniform(-0.2, 0.2) if jitter_value is None else jitter_value
    return max(0.0, base * (1 + jitter))


async def submit_task(
    definition: str,
    task_data: dict,
    description: str = "",
    priority: int = 0,
    run_at: datetime | None = None,
    *,
    idempotency_key: str | None = None,
    correlation_id: str = "",
    event_id: int | None = None,
) -> int:
    """按已登记定义提交任务；只有显式幂等键才执行去重。"""
    declaration = task_registry.require(definition)
    task = TaskQueue(
        type=definition,
        definition=definition,
        owner=declaration.owner,
        group_name=declaration.group,
        status="Wait",
        priority=priority,
        retry=0,
        max_retry=max(declaration.max_attempts - 1, 0),
        attempt=0,
        max_attempts=declaration.max_attempts,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        event_id=event_id,
        task_data=json.dumps(task_data, ensure_ascii=False, sort_keys=True),
        description=description or declaration.title,
        run_at=run_at,
        next_run_at=run_at or datetime.now(),
        create_time=datetime.now(),
    )
    async with async_session_factory() as db:
        db.add(task)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if not idempotency_key:
                raise
            existing = (await db.execute(select(TaskQueue.id).where(
                TaskQueue.definition == definition,
                TaskQueue.idempotency_key == idempotency_key,
            ))).scalar_one()
            return int(existing)
        await db.refresh(task)
    logger.info("[TaskQueue] 已提交: id=%s definition=%s", task.id, definition)
    return int(task.id)


async def pause_owner_tasks(owner: str) -> int:
    return await _transition_owner(owner, ("Wait",), "Paused", "插件已禁用")


async def resume_owner_tasks(owner: str) -> int:
    return await _transition_owner(owner, ("Paused",), "Wait", "")


async def cancel_owner_tasks(owner: str) -> int:
    return await _transition_owner(owner, ("Wait", "Paused"), "Cancelled", "插件已卸载")


async def _transition_owner(owner: str, source: tuple[str, ...], target: str, reason: str) -> int:
    async with async_session_factory() as db:
        result = cast(CursorResult[Any], await db.execute(update(TaskQueue).where(
            TaskQueue.owner == owner, TaskQueue.status.in_(source),
        ).values(status=target, error_msg=reason, version=TaskQueue.version + 1)))
        await db.commit()
    return int(result.rowcount or 0)


class TaskQueueWorker:
    """单进程轮询、批次并发执行的持久化任务 Worker。"""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._running:
            return
        if not task_registry.definitions():
            raise RuntimeError("任务定义为空，拒绝启动 Worker")
        self._stop_event = asyncio.Event()
        try:
            await self._recover_interrupted_tasks()
        except Exception:
            self._stop_event = None
            raise
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[TaskQueueWorker] Worker 已启动，定义数=%d", len(task_registry.definitions()))

    async def stop(self) -> None:
        self._running = False
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, _WORKER_STOP_TIMEOUT_SECONDS)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("[TaskQueueWorker] 关闭等待超时或被取消")
        self._task = None
        self._stop_event = None

    async def _loop(self) -> None:
        while self._running:
            try:
                if not await _get_config("task_queue_enabled", 1):
                    if await self._wait_or_stop(10):
                        break
                    continue
                dispatched = await self._dispatch_outbox()
                processed = await self._process_batch()
                if not dispatched and not processed:
                    interval = await _get_config("task_queue_poll_interval", 3)
                    if await self._wait_or_stop(float(interval)):
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[TaskQueueWorker] 轮询异常")
                if await self._wait_or_stop(5):
                    break

    async def _wait_or_stop(self, seconds: float) -> bool:
        if not self._stop_event:
            await asyncio.sleep(seconds)
            return not self._running
        try:
            await asyncio.wait_for(self._stop_event.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _dispatch_outbox(self) -> bool:
        from core.events.registry import event_registry

        async with async_session_factory() as db:
            rows = (await db.execute(select(EventOutbox).where(
                EventOutbox.status == "Pending",
            ).order_by(EventOutbox.id).limit(20))).scalars().all()
            for row in rows:
                payload = json.loads(row.payload)
                for subscription in event_registry.subscriptions(row.event_name):
                    await submit_task(
                        subscription.task_name,
                        {"event_name": row.event_name, "payload": payload},
                        description=subscription.title or row.event_name,
                        idempotency_key=f"event:{row.id}:{subscription.task_name}",
                        correlation_id=row.correlation_id,
                        event_id=row.id,
                    )
                row.status = "Dispatched"
                row.dispatched_at = datetime.now()
            if rows:
                await db.commit()
        return bool(rows)

    async def _process_batch(self) -> bool:
        batch_size = int(await _get_config("task_queue_batch_size", 10))
        now = datetime.now()
        async with async_session_factory() as db:
            rows = (await db.execute(select(TaskQueue).where(
                TaskQueue.status == "Wait",
                or_(TaskQueue.next_run_at.is_(None), TaskQueue.next_run_at <= now),
            ).order_by(TaskQueue.priority, TaskQueue.id).limit(batch_size))).scalars().all()
        if not rows:
            return False
        limits: dict[str, int] = {}
        for row in rows:
            declaration = task_registry.get(row.definition or row.type)
            if declaration:
                limits[declaration.group] = min(
                    limits.get(declaration.group, declaration.concurrency),
                    declaration.concurrency,
                )
        semaphores = {name: asyncio.Semaphore(limit) for name, limit in limits.items()}
        await asyncio.gather(*(self._process_one(row, semaphores) for row in rows))
        if await _get_config("task_queue_clean_finish", 1):
            await self._clean_finished()
        return True

    async def _process_one(self, task: TaskQueue, semaphores: dict[str, asyncio.Semaphore]) -> None:
        definition_name = task.definition or task.type
        declaration = task_registry.get(definition_name)
        if declaration is None:
            await self._mark_unavailable(task.id, f"任务定义未注册: {definition_name}")
            return
        semaphore = semaphores[declaration.group]
        async with semaphore:
            attempt = task.attempt + 1
            if not await self._claim(task, attempt):
                return
            await self._execute(task, declaration, attempt)

    async def _claim(self, task: TaskQueue, attempt: int) -> bool:
        async with async_session_factory() as db:
            result = cast(CursorResult[Any], await db.execute(update(TaskQueue).where(
                TaskQueue.id == task.id, TaskQueue.version == task.version,
                TaskQueue.status == "Wait",
            ).values(
                version=task.version + 1, status="Exec", attempt=attempt,
                retry=attempt, start_time=datetime.now(), locked_at=datetime.now(),
            )))
            await db.commit()
        return bool(result.rowcount)

    async def _execute(self, task: TaskQueue, declaration: TaskDefinition, attempt: int) -> None:
        started_at = datetime.now()
        start = time.perf_counter()
        error = ""
        try:
            context = TaskContext(
                task_id=task.id, owner=declaration.owner, attempt=attempt,
                max_attempts=task.max_attempts or declaration.max_attempts,
                idempotency_key=task.idempotency_key,
                correlation_id=task.correlation_id or "", event_id=task.event_id,
            )
            task_data = json.loads(task.task_data)
            if inspect.iscoroutinefunction(declaration.handler):
                await asyncio.wait_for(
                    declaration.handler(context, task_data),
                    declaration.timeout_seconds,
                )
            else:
                await asyncio.wait_for(
                    asyncio.to_thread(declaration.handler, context, task_data),
                    declaration.timeout_seconds,
                )
            final_status = "Finish"
        except asyncio.TimeoutError:
            error = f"执行超时（{declaration.timeout_seconds}秒）"
            final_status = self._failure_status(task, declaration, attempt)
        except Exception as exc:
            error = str(exc)
            final_status = self._failure_status(task, declaration, attempt)
            logger.exception("[TaskQueueWorker] 执行失败: id=%s definition=%s", task.id, declaration.name)
        duration_ms = int((time.perf_counter() - start) * 1000)
        await self._record_result(task, declaration, attempt, final_status, error, started_at, duration_ms)
        if final_status == "Dead" and declaration.failure_notifications:
            await self._publish_failure(task, declaration, attempt, error)

    @staticmethod
    def _failure_status(task: TaskQueue, declaration: TaskDefinition, attempt: int) -> str:
        return "Dead" if attempt >= (task.max_attempts or declaration.max_attempts) else "Wait"

    async def _record_result(self, task, declaration, attempt, status, error, started_at, duration_ms) -> None:
        next_run = None
        if status == "Wait":
            delay = retry_delay_seconds(attempt) if declaration.backoff else 0
            next_run = datetime.now() + timedelta(seconds=delay)
        async with async_session_factory() as db:
            await db.execute(update(TaskQueue).where(TaskQueue.id == task.id).values(
                status=status, error_msg=error, finish_time=datetime.now(),
                next_run_at=next_run, locked_at=None,
            ))
            db.add(TaskLog(
                task_id=task.id, owner=declaration.owner, definition=declaration.name,
                attempt=attempt, correlation_id=task.correlation_id or "", event_id=task.event_id,
                task_name=declaration.name, task_desc=task.description or declaration.title,
                task_type=declaration.owner, status="success" if status == "Finish" else "failed",
                error_msg=error or None, duration_ms=duration_ms,
                start_time=started_at, end_time=datetime.now(), create_time=datetime.now(),
            ))
            await db.commit()

    async def _publish_failure(self, task, declaration, attempt, error) -> None:
        from core.events import event_bus

        async with async_session_factory() as db:
            await event_bus.publish_durable("task.failed", {
                "task_id": task.id, "owner": declaration.owner,
                "definition": declaration.name, "attempt": attempt,
                "max_attempts": task.max_attempts or declaration.max_attempts,
                "error_msg": error, "correlation_id": task.correlation_id or "",
                "event_id": task.event_id,
            }, db, correlation_id=task.correlation_id or "")
            await db.commit()

    async def _mark_unavailable(self, task_id: int, reason: str) -> None:
        async with async_session_factory() as db:
            await db.execute(update(TaskQueue).where(TaskQueue.id == task_id).values(
                status="Paused", error_msg=reason, locked_at=None,
            ))
            await db.commit()

    async def _recover_interrupted_tasks(self) -> int:
        async with async_session_factory() as db:
            result = cast(CursorResult[Any], await db.execute(update(TaskQueue).where(
                TaskQueue.status == "Exec",
            ).values(
                status="Wait", version=TaskQueue.version + 1,
                error_msg=_INTERRUPTED_TASK_REASON, locked_at=None,
                start_time=None, finish_time=None, next_run_at=datetime.now(),
            )))
            await db.commit()
        return int(result.rowcount or 0)

    async def _clean_finished(self) -> None:
        async with async_session_factory() as db:
            await db.execute(delete(TaskQueue).where(TaskQueue.status == "Finish"))
            await db.commit()


queue_worker = TaskQueueWorker()
