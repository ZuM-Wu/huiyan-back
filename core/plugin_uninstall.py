"""插件卸载时由框架统一执行的数据库清理。"""

import logging

from core.config_manager import ConfigManager
from core.hook_events import emit_plugin_uninstalled

logger = logging.getLogger(__name__)


async def _uninstall_plugin(manager, name: str, db, router_manager=None) -> bool:
    """使用数据库专用实例编排完整卸载，失败后保持插件禁用。"""
    from core.hardware_catalog import ensure_hardware_uninstallable
    await ensure_hardware_uninstallable(name, db)
    plugin_cls, metadata = manager._load_plugin_class(name)
    if not plugin_cls:
        return False
    instance = plugin_cls(db, metadata.get("config", {}))
    try:
        if await instance.can_uninstall() is not True:
            logger.warning("插件 '%s' 拒绝卸载，未产生卸载副作用", name)
            return False
    except Exception as exc:
        logger.error("插件 '%s' 卸载前置检查失败: %s", name, exc, exc_info=True)
        return False

    # 停止新采集并等待已开始的短提交，然后复核设备数量，封闭同步与卸载之间的竞态。
    from core.hardware_provider import hardware_provider_registry
    await hardware_provider_registry.block_owner(name)
    try:
        await ensure_hardware_uninstallable(name, db)
    except Exception:
        hardware_provider_registry.unblock_owner(name)
        raise

    uninstall_started = False
    try:
        from core.platform.plugin_lifecycle import begin_uninstall, finalize_lifecycle

        await begin_uninstall(name, router_manager)
        uninstall_started = True
        runtime_instance = manager._loaded.get(name)
        if runtime_instance:
            await runtime_instance.on_runtime_disable()
        manager._unregister_runtime_capabilities(name)
        manager._loaded.pop(name, None)

        if await instance.uninstall() is not True:
            raise RuntimeError(f"插件 '{name}' 私有清理返回失败")
        await _cleanup_plugin_records(name, db, metadata)
        await db.commit()
        await finalize_lifecycle(name, "uninstalled")
        await emit_plugin_uninstalled(name)
        return True
    except Exception as exc:
        await db.rollback()
        if not uninstall_started:
            hardware_provider_registry.unblock_owner(name)
        if uninstall_started:
            manager._unregister_runtime_capabilities(name)
            manager._loaded.pop(name, None)
            try:
                await _mark_plugin_disabled(name, db)
            except Exception:
                await db.rollback()
                logger.exception("插件 '%s' 卸载失败状态持久化失败", name)
        logger.error("插件 '%s' 卸载异常: %s", name, exc, exc_info=True)
        return False


async def _mark_plugin_disabled(name: str, db) -> None:
    """卸载开始后发生失败时持久化禁用状态，防止重启重新加载。"""
    from sqlalchemy import update

    from core.db.plugin import PluginModel

    await db.execute(
        update(PluginModel).where(PluginModel.name == name).values(status=2)
    )
    await db.commit()


async def _cleanup_plugin_records(name: str, db, metadata: dict) -> None:
    """清理插件权限、配置、导航、菜单和安装记录，不提交事务。"""
    from sqlalchemy import delete, or_

    from core.auth.rbac import unregister_plugin_permissions
    from core.db.menu import Menu
    from core.db.nav import Nav
    from core.db.plugin import PluginModel
    from core.db.plugin_database_state import PluginDatabaseStateModel

    await unregister_plugin_permissions(name)
    extra_prefixes = {
        f"{key.rsplit('.', 1)[0]}."
        for key in (metadata.get("config") or {})
        if isinstance(key, str) and "." in key
    }
    await ConfigManager().delete_plugin_config(name, db, extra_prefixes=extra_prefixes)
    await db.execute(delete(Menu).where(or_(
        Menu.plugin == name,
        Menu.path.like(f"%/plugin/{name}/%"),
    )))
    await db.execute(delete(Nav).where(Nav.plugin == name))
    # 卸载后删除平台体检快照，避免已移除插件继续显示为版本不一致。
    await db.execute(delete(PluginDatabaseStateModel).where(
        PluginDatabaseStateModel.plugin_name == name
    ))
    await db.execute(delete(PluginModel).where(PluginModel.name == name))
