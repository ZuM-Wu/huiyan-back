"""主题和插件生命周期事件门面。"""

from typing import Any

from core.db.base import async_session_factory
from core.events import event_bus


async def publish_durable(name: str, payload: dict[str, Any], correlation_id: str = "") -> int:
    """在独立事务中写入可靠事件 Outbox。"""
    async with async_session_factory() as db:
        event_id = await event_bus.publish_durable(
            name, payload, db, correlation_id=correlation_id
        )
        await db.commit()
        return event_id


async def publish_transient(name: str, payload: dict[str, Any]) -> list[dict]:
    """发布不阻断调用方的瞬时事件。"""
    return await event_bus.publish_transient(name, payload)


async def publish_lifecycle_event(
    name: str, payload: dict[str, Any], *, durable: bool = True, correlation_id: str = ""
) -> int | list[dict]:
    """按事件目录选择可靠或瞬时投递。"""
    if durable:
        return await publish_durable(name, payload, correlation_id)
    return await publish_transient(name, payload)
