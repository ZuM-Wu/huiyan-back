"""插件运行态快照与版本读取辅助。"""

from core.platform.audit import audit_log
from core.platform.event import publish_lifecycle_event
from core.platform.health import platform_health
from core.platform.resource import resource_registry
from core.plugin_manager import PluginManager


async def runtime_snapshot(platform, plugin_id: str, *, manager=None, router_manager=None) -> dict:
    """返回插件运行状态，不加载或替换插件 Python 实现。"""
    manager = manager or PluginManager()
    try:
        metadata = manager.load_metadata(plugin_id)
    except Exception:
        metadata = {}
    registered = bool(router_manager and router_manager.is_registered(plugin_id))
    disabled = bool(router_manager and plugin_id in getattr(router_manager, "_disabled", set()))
    plan = await platform.get_update_status(plugin_id)
    return {
        "plugin_id": plugin_id, "owner": plugin_id,
        "version": str(metadata.get("version") or ""),
        "owner_gate": "disabled" if disabled else "enabled",
        "routes_registered": registered, "pages": len(declared_pages(manager, plugin_id)),
        "resources": len(resource_registry.snapshot(plugin_id)),
        "tasks": await count_owner_tasks(plugin_id), "events": count_owner_events(plugin_id),
        "health": platform_health.snapshot().get(f"plugin:{plugin_id}", {"status": "unknown"}),
        "last_failure": platform._last_failure.get(plugin_id, ""),
        "operation_id": plan.get("operation_id", "") if plan else "",
    }


async def record_lifecycle(plugin_id: str, action: str, *, request=None) -> None:
    """记录启停生命周期审计和事件。"""
    if action not in {"enabled", "disabled"}:
        raise ValueError(f"不支持的插件生命周期动作: {action}")
    await audit_log(f"插件{action}: {plugin_id}", f"plugin_{action}", owner=plugin_id, phase=action, request=request)
    await publish_lifecycle_event(f"plugin.{action}", {"plugin_name": plugin_id}, durable=False)


async def installed_version(plugin_id: str) -> str:
    """读取 hy_plugin 当前运行版本。"""
    from sqlalchemy import select
    from core.db.base import async_session_factory
    from core.db.plugin import PluginModel
    try:
        async with async_session_factory() as db:
            record = (await db.execute(select(PluginModel.version).where(
                PluginModel.name == plugin_id
            ))).scalar_one_or_none()
            return str(record or "")
    except Exception:
        return ""


def declared_pages(manager: PluginManager, plugin_id: str) -> list[dict]:
    """读取插件页面声明。"""
    plugin_cls, meta = manager._load_plugin_class(plugin_id)
    if not plugin_cls:
        return []
    try:
        return list(plugin_cls(None, meta.get("config", {})).get_pages() or [])
    except Exception:
        return []


async def count_owner_tasks(owner: str) -> int:
    """统计插件 owner 任务数量。"""
    from sqlalchemy import func, select
    from core.db.base import async_session_factory
    from core.db.task_queue import TaskQueue
    try:
        async with async_session_factory() as db:
            return int(await db.scalar(select(func.count(TaskQueue.id)).where(TaskQueue.owner == owner)) or 0)
    except Exception:
        return 0


def count_owner_events(owner: str) -> int:
    """统计插件 owner 事件订阅数量。"""
    from core.events import event_registry
    return sum(1 for item in event_registry.subscription_items() if item.owner == owner)
