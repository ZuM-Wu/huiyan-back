"""插件 owner 门禁和任务生命周期适配。"""

from core.platform.audit import audit_log
from core.platform.task import cancel_owner, pause_owner, resume_owner
from core.platform.plugin import plugin_platform
from core.platform.resource import resource_registry
from core.platform.health import platform_health


async def begin_uninstall(name: str, router_manager=None) -> None:
    """卸载前先拒绝新请求并取消 owner 未完成任务。"""
    if router_manager:
        router_manager.mark_disabled(name)
    try:
        await cancel_owner(name)
    except Exception as exc:
        raise RuntimeError(f"插件 '{name}' 任务取消失败: {exc}") from exc
    resource_registry.invalidate_owner(name)


def register_plugin_resources(name: str, load_metadata) -> None:
    """从 manifest 登记插件资源；缺少资源声明不阻止服务型插件启动。"""
    try:
        metadata = load_metadata(name)
        resource_registry.register_manifest(
            {**metadata, "surface": metadata.get("surface", "plugin")}, name,
        )
    except (OSError, ValueError, TypeError) as exc:
        platform_health.mark(f"plugin:{name}:resource", "degraded", str(exc))


async def pause_plugin_owner(name: str, router_manager=None) -> bool:
    """禁用前暂停 owner 任务；失败时恢复 owner gate。"""
    if router_manager:
        router_manager.mark_disabled(name)
    try:
        await pause_owner(name)
    except Exception:
        if router_manager:
            router_manager.mark_enabled(name)
        return False
    resource_registry.invalidate_owner(name)
    return True


async def resume_plugin_owner_or_disable(name: str, db, manager, plugin_model, update) -> bool:
    """恢复 owner 任务失败时原子恢复禁用状态。"""
    try:
        await resume_owner(name)
    except Exception:
        await db.execute(update(plugin_model).where(plugin_model.name == name).values(status=2))
        await db.commit()
        manager._unregister_runtime_capabilities(name)
        resource_registry.invalidate_owner(name)
        manager._loaded.pop(name, None)
        return False
    return True


async def finalize_lifecycle(name: str, action: str) -> None:
    """在数据库与运行时能力均成功后写入生命周期审计/事件。"""
    if action in {"enabled", "disabled"}:
        await plugin_platform.record_lifecycle(name, action)
    elif action == "uninstalled":
        await audit_log(f"插件卸载: {name}", "plugin_disable", owner=name, phase="uninstall")
