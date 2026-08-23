"""插件升级运行态处理，供 PluginManager 组合使用。"""

import asyncio
import importlib
import inspect
import logging
from typing import Any, List

_VALID_NAV_TYPES = frozenset({"admin", "frontend"})
logger = logging.getLogger(__name__)


class PluginUpgradeMixin:
    """封装版本迁移、运行态暂停与导航注册，保持 PluginManager 公共 API 不变。"""

    _upgrade_locks: Any
    _loaded: Any
    plugins_dir: Any
    _set_upgrade_error: Any
    _clear_upgrade_error: Any
    _invalidate_plugin_modules: Any
    _find_plugin_path: Any
    _migration_steps: Any
    _unregister_mcp_tools_runtime: Any
    load_metadata: Any
    restore_capabilities: Any
    _unregister_runtime_capabilities: Any

    async def upgrade(self, name: str, db, router_manager=None) -> bool:
        """执行版本迁移并刷新插件运行态。"""
        lock = self._upgrade_locks.setdefault(name, asyncio.Lock())
        if lock.locked():
            self._set_upgrade_error(name, "upgrade_in_progress", "插件正在升级，请稍后重试")
            return False
        async with lock:
            return await self._upgrade_locked(name, db, router_manager)

    async def _upgrade_locked(self, name: str, db, router_manager=None) -> bool:
        from core.db.plugin import PluginModel

        self._clear_upgrade_error(name)
        context = await self._load_upgrade_context(name, db, PluginModel)
        if context is None:
            return False
        record, old_version, new_version, instance, migration_steps = context
        was_enabled = getattr(record, "status", 1) == 1
        try:
            await self._pause_runtime(name, was_enabled, router_manager)
        except Exception as exc:
            self._set_upgrade_error(name, "runtime_refresh_failed", str(exc))
            logger.exception("插件 '%s' 升级前运行态清理失败", name)
            return False
        if not await self._run_upgrade_transaction(
            name, db, instance, old_version, new_version, migration_steps, PluginModel
        ):
            if was_enabled:
                await self._restore_runtime_after_upgrade_failure(name, router_manager)
            return False
        if not was_enabled:
            self._invalidate_plugin_modules(name)
            self._clear_upgrade_error(name)
            logger.info("禁用插件 '%s' 已完成版本升级: %s -> %s", name, old_version, new_version)
            return True
        return await self._refresh_runtime_after_upgrade(name, old_version, new_version, router_manager)

    async def _load_upgrade_context(self, name: str, db, plugin_model):
        """加载并校验升级所需的数据库记录、插件类和迁移链。"""
        from sqlalchemy import select as sa_select
        from core.plugin_manager import compare_versions

        record = (await db.execute(sa_select(plugin_model).where(plugin_model.name == name))).scalar_one_or_none()
        if not record:
            self._set_upgrade_error(name, "not_installed", f"插件 '{name}' 未安装")
            return None
        old_version = record.version
        try:
            meta = self.load_metadata(name)
            new_version = meta.get("version", "1.0.0")
            comparison = compare_versions(old_version, new_version)
            if comparison == 0:
                raise ValueError(f"插件 '{name}' 已是最新版本")
            if comparison > 0:
                raise ValueError(f"磁盘版本 {new_version} 低于已安装版本 {old_version}，拒绝降级")
            rel_path = self._find_plugin_path(name)
            module_name = rel_path.split("/")[0]
            module = importlib.import_module(f"plugins.{module_name}.{name}.plugin")
            plugin_cls = getattr(module, "Plugin", None)
            if not plugin_cls:
                raise ValueError(f"插件 '{name}' 缺少 Plugin 类")
            migration_steps = self._migration_steps(name, old_version, new_version)
            instance = plugin_cls(db, meta.get("config", {}))
        except (ImportError, OSError, TypeError, ValueError) as exc:
            await db.rollback()
            code = "already_latest" if "已是最新版本" in str(exc) else "downgrade_rejected" if "拒绝降级" in str(exc) else "migration_invalid"
            self._set_upgrade_error(name, code, str(exc))
            return None
        return record, old_version, new_version, instance, migration_steps

    async def _pause_runtime(self, name: str, enabled: bool, router_manager=None) -> None:
        """暂停插件运行态，避免迁移过程中继续处理请求或事件。"""
        if not enabled:
            return
        if router_manager:
            router_manager.mark_disabled(name)
        runtime_instance = self._loaded.get(name)
        if runtime_instance:
            callback = getattr(runtime_instance, "on_runtime_disable", None)
            if callback:
                await callback()
        self._unregister_runtime_capabilities(name)
        self._loaded.pop(name, None)

    async def _run_upgrade_transaction(self, name, db, instance, old_version, new_version, migration_steps, plugin_model) -> bool:
        """在单一事务中执行 SQL、插件钩子和版本写回。"""
        from sqlalchemy import select as sa_select, update as sa_update

        try:
            for migration_path in migration_steps:
                with open(migration_path, "r", encoding="utf-8") as migration_file:
                    sql = migration_file.read()
                if sql.strip():
                    await instance._exec_sql(sql)
            upgrade_result = instance.upgrade(old_version)
            if inspect.isawaitable(upgrade_result):
                upgrade_result = await upgrade_result
            if upgrade_result is False:
                raise ValueError(f"插件 '{name}' upgrade() 返回失败")
            await db.execute(sa_update(plugin_model).where(plugin_model.name == name).values(version=new_version))
            await db.commit()
            persisted = await db.execute(sa_select(plugin_model.version).where(plugin_model.name == name))
            if persisted.scalar_one_or_none() != new_version:
                raise RuntimeError(f"插件 '{name}' 版本写回校验失败")
            return True
        except Exception as exc:
            await db.rollback()
            self._set_upgrade_error(name, "migration_failed", str(exc))
            logger.error("插件 '%s' 升级失败(已回滚): %s", name, exc, exc_info=True)
            return False

    async def _refresh_runtime_after_upgrade(self, name, old_version, new_version, router_manager):
        """重新加载插件模块、路由、Hook 和 MCP 工具。"""
        try:
            self._invalidate_plugin_modules(name)
            if router_manager and not router_manager.refresh_plugin_router(name):
                raise RuntimeError("插件路由刷新失败")
            if not self.restore_capabilities(name):
                raise RuntimeError("插件运行时能力刷新失败")
            runtime_instance = self._loaded.get(name)
            if runtime_instance:
                await runtime_instance.on_runtime_enable()
            if router_manager:
                router_manager.mark_enabled(name)
            self._clear_upgrade_error(name)
            logger.info("插件 '%s' 升级成功: %s -> %s", name, old_version, new_version)
            return True
        except Exception as exc:
            if router_manager:
                router_manager.mark_disabled(name)
            self._set_upgrade_error(name, "runtime_refresh_failed", str(exc))
            logger.error("插件 '%s' 运行态刷新失败，已保持禁用: %s", name, exc, exc_info=True)
            return False

    async def _restore_runtime_after_upgrade_failure(self, name: str, router_manager=None) -> None:
        """迁移事务失败时恢复旧 Hook，并重新放行原本启用的插件。"""
        try:
            self.restore_capabilities(name)
            runtime_instance = self._loaded.get(name)
            if runtime_instance:
                await runtime_instance.on_runtime_enable()
            if router_manager:
                router_manager.mark_enabled(name)
        except Exception:
            logger.exception("插件 '%s' 升级回滚后的运行态恢复失败", name)

    async def _register_plugin_nav(self, name: str, instance, meta: dict, db) -> None:
        """将插件页面声明幂等写入导航表。"""
        from core.db.nav import Nav
        from sqlalchemy import select as sa_select

        if meta.get("module", getattr(instance, "module", "")) != "addon":
            return
        pages = instance.get_pages() or []
        self._validate_plugin_pages(name, pages)
        from core.plugin_page_validator import validate_plugin_page_sources
        plugin_root = self.plugins_dir / self._find_plugin_path(name)
        validate_plugin_page_sources(plugin_root, pages)
        for page in pages:
            if page.get("hidden"):
                continue
            nav_type = page.get("nav_type", "admin")
            key = page.get("key") or f"plugin_{name}"
            existing = (await db.execute(sa_select(Nav).where(Nav.key == key, Nav.nav_type == nav_type))).scalar_one_or_none()
            if existing:
                continue
            db.add(Nav(key=key, title=page.get("title") or meta.get("title", name), path=page.get("path", ""),
                       icon=page.get("icon", "app"), nav_type=nav_type, source="plugin", plugin=name,
                       group_name=meta.get("title", name), page_type="system"))

    @staticmethod
    def _validate_plugin_pages(name: str, pages: List[dict]) -> None:
        """校验插件页面元数据，确保导航渲染符合插件页面规范。"""
        if not isinstance(pages, list):
            raise ValueError(f"插件 '{name}' 的 get_pages() 必须返回列表")
        seen_keys = set()
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                raise ValueError(f"插件 '{name}' 的第 {index + 1} 个页面必须是对象")
            key, title, path, icon = page.get("key"), page.get("title"), page.get("path"), page.get("icon")
            nav_type = page.get("nav_type", "admin")
            if not isinstance(key, str) or not key.strip() or key in seen_keys:
                raise ValueError(f"插件 '{name}' 的页面 key 无效或重复: {key}")
            if not isinstance(title, str) or not title.strip() or not isinstance(icon, str) or not icon.strip():
                raise ValueError(f"插件 '{name}' 的页面标题和图标不能为空")
            if nav_type not in _VALID_NAV_TYPES:
                raise ValueError(f"插件 '{name}' 的页面 nav_type 无效: {nav_type}")
            audience = page.get("audience") or ("admin" if nav_type == "admin" else "farmer")
            if audience not in {"admin", "farmer"}:
                raise ValueError(f"插件 '{name}' 的页面 audience 无效: {audience}")
            if (audience == "admin") != (nav_type == "admin"):
                raise ValueError(f"插件 '{name}' 的页面 audience 与 nav_type 不一致")
            route_root = "admin" if nav_type == "admin" else "farmer"
            prefix = f"/{route_root}/plugin/{name}/"
            if (
                not isinstance(path, str)
                or not path.startswith(prefix)
                or not path[len(prefix):].strip("/")
                or ".." in path.split("/")
                or any(char.isspace() for char in path)
            ):
                raise ValueError(f"插件 '{name}' 的页面 path 必须符合 {prefix}<page>")
            template = page.get("template") or f"{path.rstrip('/').split('/')[-1]}.html"
            if not isinstance(template, str) or not template.endswith(".html") or any(
                marker in template for marker in ("/", "\\", "..")
            ):
                raise ValueError(f"插件 '{name}' 的页面 template 无效: {template}")
            for field in ("styles", "scripts"):
                assets = page.get(field, [])
                if not isinstance(assets, list) or any(
                    not isinstance(asset, str) or not asset or asset.startswith(("/", "http:" , "https:"))
                    or ".." in asset.replace("\\", "/").split("/")
                    for asset in assets
                ):
                    raise ValueError(f"插件 '{name}' 的页面 {field} 声明无效")
            api_base = page.get("api_base", "")
            if not isinstance(api_base, str) or not api_base.startswith("/api/"):
                raise ValueError(f"插件 '{name}' 的页面 api_base 必须是站内 API 路径")
            seen_keys.add(key)
