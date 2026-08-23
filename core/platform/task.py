"""主题和插件更新任务门面。

所有任务最终进入现有 MySQL 队列，平台层只负责登记稳定定义和 owner 操作。
"""

import asyncio
from typing import Any

from services.task.definitions import TaskContext, TaskDefinition, task_registry


async def _prepare_update(context: TaskContext, data: dict[str, Any]) -> None:
    from core.platform.plugin import plugin_platform

    plugin_platform.validate_local_package(
        data.get("plugin_id", ""), data.get("package_ref") or ""
    )


async def _pause_owner(context: TaskContext, data: dict[str, Any]) -> None:
    from services.task.queue_worker import pause_owner_tasks

    await pause_owner_tasks(str(data.get("owner") or ""))


async def _resume_owner(context: TaskContext, data: dict[str, Any]) -> None:
    from services.task.queue_worker import resume_owner_tasks

    await resume_owner_tasks(str(data.get("owner") or ""))


async def _cancel_owner(context: TaskContext, data: dict[str, Any]) -> None:
    from services.task.queue_worker import cancel_owner_tasks

    await cancel_owner_tasks(str(data.get("owner") or ""))


async def _validate_theme(context: TaskContext, data: dict[str, Any]) -> None:
    from core.platform.theme import theme_platform

    theme_platform.validate_theme_package(
        data.get("surface", ""), data.get("package_ref") or ""
    )


async def _activate_theme(context: TaskContext, data: dict[str, Any]) -> None:
    # 本地主题激活通常是同步快照切换；该定义为未来耗时资源激活保留稳定任务名。
    if not data.get("surface"):
        raise ValueError("主题激活缺少 surface")


_PLATFORM_DEFINITIONS = (
    TaskDefinition(
        "plugin.update.prepare", "插件更新预检", "platform.plugin", "plugin-update",
        _prepare_update, timeout_seconds=120, max_attempts=1, concurrency=1,
    ),
    TaskDefinition(
        "plugin.owner.pause", "暂停插件任务", "platform.plugin", "plugin-lifecycle",
        _pause_owner, timeout_seconds=30, max_attempts=2, concurrency=1,
    ),
    TaskDefinition(
        "plugin.owner.resume", "恢复插件任务", "platform.plugin", "plugin-lifecycle",
        _resume_owner, timeout_seconds=30, max_attempts=2, concurrency=1,
    ),
    TaskDefinition(
        "plugin.owner.cancel", "取消插件任务", "platform.plugin", "plugin-lifecycle",
        _cancel_owner, timeout_seconds=30, max_attempts=2, concurrency=1,
    ),
    TaskDefinition(
        "theme.resource.validate", "主题资源校验", "platform.theme", "theme-resource",
        _validate_theme, timeout_seconds=120, max_attempts=1, concurrency=1,
    ),
    TaskDefinition(
        "theme.resource.activate", "主题资源激活", "platform.theme", "theme-resource",
        _activate_theme, timeout_seconds=60, max_attempts=1, concurrency=1,
    ),
)


def ensure_platform_tasks() -> None:
    """幂等登记平台任务；启动和独立 API 测试均可调用。"""
    for definition in _PLATFORM_DEFINITIONS:
        current = task_registry.get(definition.name)
        if current is None:
            task_registry.register(definition)
        elif current != definition:
            raise ValueError(f"平台任务定义冲突: {definition.name}")


async def submit_platform_task(
    definition: str,
    task_data: dict[str, Any],
    *,
    description: str = "",
    idempotency_key: str | None = None,
    correlation_id: str = "",
) -> int:
    """通过统一队列提交主题/插件平台任务。"""
    ensure_platform_tasks()
    from services.task.queue_worker import submit_task

    return await submit_task(
        definition,
        task_data,
        description=description,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )


async def pause_owner(owner: str) -> int:
    """暂停 owner 的待执行任务。"""
    from services.task.queue_worker import pause_owner_tasks

    return await pause_owner_tasks(owner)


async def resume_owner(owner: str) -> int:
    """恢复 owner 的暂停任务。"""
    from services.task.queue_worker import resume_owner_tasks

    return await resume_owner_tasks(owner)


async def cancel_owner(owner: str) -> int:
    """取消 owner 的未完成任务。"""
    from services.task.queue_worker import cancel_owner_tasks

    return await cancel_owner_tasks(owner)


async def query_queue(**filters) -> dict:
    """查询现有持久化任务队列，不直接暴露 ORM。"""
    from core.task_query_service import list_task_queue

    return await list_task_queue(**filters)


async def query_task(task_id: int) -> dict | None:
    """查询单条持久化队列任务。"""
    from core.task_query_service import get_task_queue_item

    return await get_task_queue_item(task_id)


async def retry_task(task_id: int) -> bool:
    """复用现有死信重试门面。"""
    from core.task_query_service import retry_queue_task

    return await retry_queue_task(task_id)


async def query_logs(**filters) -> dict:
    """查询现有任务执行日志。"""
    from core.task_query_service import list_task_logs

    return await list_task_logs(**filters)


async def drain_owner(owner: str, timeout_seconds: float = 30.0) -> int:
    """等待 owner 的运行中任务结束；超时明确失败，不伪装排空成功。"""
    from sqlalchemy import func, select

    from core.db.base import async_session_factory
    from core.db.task_queue import TaskQueue

    deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 0.1)
    while True:
        async with async_session_factory() as db:
            running = int(await db.scalar(select(func.count(TaskQueue.id)).where(
                TaskQueue.owner == owner, TaskQueue.status == "Exec",
            )) or 0)
        if running == 0:
            return running
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"插件 owner 任务排空超时: {owner}")
        await asyncio.sleep(0.1)
