"""系统内置任务定义。"""

from services.task.definitions import TaskDefinition, task_registry


async def _handle_notice(context, task_data: dict) -> None:
    from services.task.notice_worker import send_notice_task

    log_id = task_data.get("log_id", 0)
    payload = task_data.get("payload") or {}
    if not log_id or not payload:
        raise ValueError("通知任务缺少 log_id 或 payload")
    await send_notice_task(log_id, payload)


async def _call(handler, context, task_data: dict) -> None:
    await handler(task_data)


def register_builtin_task_definitions() -> None:
    """登记通知及周期任务；重复登记同一对象保持幂等。"""
    task_registry.register(TaskDefinition(
        name="notice", title="通知发送", owner="system", group="notice",
        handler=_handle_notice, timeout_seconds=30, max_attempts=3,
        concurrency=4, failure_notifications=False,
    ))

    from services.task.periodic_handlers import periodic_handlers

    for name, title, owner, handler in periodic_handlers():
        if task_registry.get(name):
            continue
        async def wrapped(context, task_data, current=handler):
            await _call(current, context, task_data)

        task_registry.register(TaskDefinition(
            name=name, title=title, owner=owner, group=owner,
            handler=wrapped, timeout_seconds=300, max_attempts=3,
            concurrency=1, failure_notifications=True,
        ))
