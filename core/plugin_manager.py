"""
插件管理器 — 慧眼护农 V4 核心骨架

BasePlugin: 插件抽象基类，install() 必须完成五步操作
PluginManager: 插件发现、安装、卸载、启用、禁用、升级
"""

import asyncio
import importlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List

from sqlalchemy import delete

from core.config import BASE_DIR, settings
from core.hook_events import emit_plugin_installed, emit_plugin_uninstalled
from core.plugin_base import BasePlugin
from core.plugin_upgrade import PluginUpgradeMixin

logger = logging.getLogger(__name__)

_VERSION_PATTERN = re.compile(
    r"^(?P<core>\d+(?:\.\d+)*)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_MIGRATION_PATTERN = re.compile(r"^upgrade_(?P<old>[^_]+)_(?P<new>[^_]+)\.sql$")
_VALID_NAV_TYPES = frozenset({"admin", "frontend"})
_REMOVED_PLUGIN_NAMES: frozenset[str] = frozenset()


class PluginUpgradeError:
    """插件升级失败的稳定错误信息，供 API 层映射 HTTP 状态码。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def version_key(value: str) -> tuple:
    """解析插件版本号，支持数字版本、预发布标识和构建元数据。"""
    if not isinstance(value, str):
        raise ValueError(f"版本号必须是字符串: {value!r}")
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"无效插件版本号: {value}")
    core = tuple(int(part) for part in match.group("core").split("."))
    core = core + (0,) * max(0, 3 - len(core))
    pre = match.group("pre")
    # 正式版本高于同核心版本的任意预发布版本。
    pre_key = (1,) if pre is None else (0, tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in pre.split(".")
    ))
    return core, pre_key


def compare_versions(left: str, right: str) -> int:
    """比较两个插件版本号，返回 -1、0 或 1。"""
    left_key = version_key(left)
    right_key = version_key(right)
    return (left_key > right_key) - (left_key < right_key)


# 12 类插件目录名
PLUGIN_MODULES = [
    "addon", "gateway", "sms", "mail", "captcha", "certification",
    "oauth", "oss", "server", "widget", "weather", "llm",
]
class PluginManager(PluginUpgradeMixin):
    """
    插件管理器
    负责插件的自动发现、加载、安装、卸载、启用、禁用
    """

    def __init__(self, plugins_dir: str | None = None):
        # 显式传入时用原值（测试可指定临时目录），否则基于 BASE_DIR 拼绝对路径
        self.plugins_dir = Path(plugins_dir) if plugins_dir else BASE_DIR / settings.PLUGINS_DIR
        self._loaded: Dict[str, BasePlugin] = {}
        self._upgrade_locks: Dict[str, asyncio.Lock] = {}
        self._upgrade_errors: Dict[str, PluginUpgradeError] = {}

    def get_upgrade_error(self, name: str) -> PluginUpgradeError | None:
        """读取最近一次升级错误，供管理 API 返回稳定错误码。"""
        return self._upgrade_errors.get(name)

    @staticmethod
    def compare_versions(left: str, right: str) -> int:
        """比较两个插件版本号，供管理 API 通过公开管理器入口调用。"""
        return compare_versions(left, right)

    def get_config_schema(self, name: str) -> List[dict]:
        """读取插件配置 Schema，隐藏插件模块定位和实例化细节。"""
        plugin_cls, meta = self._load_plugin_class(name)
        if not plugin_cls:
            raise ValueError(f"插件 '{name}' 不存在或缺少 Plugin 类")
        instance = plugin_cls(None, meta.get("config", {}))
        schema = instance.get_config_schema() or []
        if not isinstance(schema, list):
            raise ValueError(f"插件 '{name}' 的配置 Schema 必须是列表")
        return schema

    def get_upload_policy_schema(self, name: str) -> List[dict]:
        """读取插件上传策略声明，隐藏插件实例化细节。"""
        plugin_cls, meta = self._load_plugin_class(name)
        if not plugin_cls:
            raise ValueError(f"插件 '{name}' 不存在或缺少 Plugin 类")
        instance = plugin_cls(None, meta.get("config", {}))
        schema = instance.get_upload_policy_schema() or []
        if not isinstance(schema, list):
            raise ValueError(f"插件 '{name}' 的上传策略 Schema 必须是列表")
        return schema

    def _set_upgrade_error(self, name: str, code: str, message: str) -> None:
        self._upgrade_errors[name] = PluginUpgradeError(code, message)

    def _clear_upgrade_error(self, name: str) -> None:
        self._upgrade_errors.pop(name, None)

    def _invalidate_plugin_modules(self, name: str) -> None:
        """清理插件模块缓存，同时保留已注册到 SQLAlchemy 的 ORM 模型。"""
        rel_path = self._find_plugin_path(name)
        module_name = rel_path.split("/")[0]
        prefix = f"plugins.{module_name}.{name}"
        models_prefix = f"{prefix}.models"
        for loaded_name in list(sys.modules):
            is_plugin_module = (
                loaded_name == prefix or loaded_name.startswith(f"{prefix}.")
            )
            is_models_module = (
                loaded_name == models_prefix
                or loaded_name.startswith(f"{models_prefix}.")
            )
            if is_plugin_module and not is_models_module:
                sys.modules.pop(loaded_name, None)
        importlib.invalidate_caches()

    def _migration_steps(self, name: str, old_version: str, new_version: str) -> list[Path]:
        """解析从旧版本到新版本的连续迁移脚本。"""
        rel_path = self._find_plugin_path(name)
        migration_dir = self.plugins_dir / rel_path / "migrations"
        if not migration_dir.is_dir():
            return []

        edges: dict[str, list[tuple[str, Path]]] = {}
        for path in migration_dir.iterdir():
            if not path.is_file():
                continue
            match = _MIGRATION_PATTERN.fullmatch(path.name)
            if not match:
                continue
            source = match.group("old")
            target = match.group("new")
            try:
                if compare_versions(source, target) >= 0:
                    continue
            except ValueError:
                logger.warning("忽略无效插件迁移文件: %s", path)
                continue
            edges.setdefault(source, []).append((target, path))

        # 没有从当前版本出发的迁移文件时，允许纯代码版本升级。
        if old_version not in edges:
            return []

        steps: list[Path] = []
        current = old_version
        visited = set()
        while compare_versions(current, new_version) < 0:
            if current in visited:
                raise ValueError(f"插件 {name} 的迁移链存在循环: {current}")
            visited.add(current)
            candidates = [
                (target, path) for target, path in edges.get(current, [])
                if compare_versions(target, new_version) <= 0
            ]
            if not candidates:
                raise ValueError(
                    f"插件 {name} 缺少迁移步骤: {current} -> {new_version}"
                )
            target, path = max(candidates, key=lambda item: version_key(item[0]))
            steps.append(path)
            current = target

        if compare_versions(current, new_version) != 0:
            raise ValueError(f"插件 {name} 迁移链未到达目标版本: {current} -> {new_version}")
        return steps

    def discover(self) -> List[dict]:
        """
        自动发现插件
        扫描 plugins/ 下 11 个类型子目录，查找含 plugin.json 的插件目录

        返回: [{"name": "hello_world", "module": "addon", "path": "addon/hello_world"}, ...]
        """
        discovered: list[dict] = []
        if not self.plugins_dir.exists():
            logger.warning(f"插件目录不存在: {self.plugins_dir}")
            return discovered

        for module_name in PLUGIN_MODULES:
            type_dir = self.plugins_dir / module_name
            if not type_dir.is_dir():
                continue
            for plugin_dir in type_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue
                if plugin_dir.name.startswith("_") or plugin_dir.name.startswith("."):
                    continue
                if plugin_dir.name in _REMOVED_PLUGIN_NAMES:
                    continue
                meta_file = plugin_dir / "plugin.json"
                if meta_file.exists():
                    discovered.append({
                        "name": plugin_dir.name,
                        "module": module_name,
                        "path": f"{module_name}/{plugin_dir.name}"
                    })
        return discovered

    def _find_plugin_path(self, name: str) -> str:
        """在 12 个类型子目录中查找插件目录"""
        for module_name in PLUGIN_MODULES:
            plugin_dir = self.plugins_dir / module_name / name
            if plugin_dir.is_dir() and (plugin_dir / "plugin.json").exists():
                return f"{module_name}/{name}"
        return name

    def load_metadata(self, name: str) -> dict:
        """
        读取插件的 plugin.json 元数据
        """
        rel_path = self._find_plugin_path(name)
        path = self.plugins_dir / rel_path / "plugin.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_plugin_class(self, name: str):
        """
        动态加载插件类与元数据（安装、卸载和能力恢复公共辅助）

        返回: (plugin_cls, meta) 元组；加载失败返回 (None, {})
        """
        try:
            rel_path = self._find_plugin_path(name)
            module_name = rel_path.split("/")[0]
            module = importlib.import_module(f"plugins.{module_name}.{name}.plugin")
            plugin_cls = getattr(module, "Plugin", None)
            if not plugin_cls:
                logger.error(f"插件 '{name}' 缺少 Plugin 类")
                return None, {}
            return plugin_cls, self.load_metadata(name)
        except Exception as e:
            logger.warning(f"加载插件 '{name}' 类失败: {e}")
            return None, {}

    def _unregister_runtime_capabilities(self, name: str) -> None:
        """按 owner 注销事件、管道、任务和 MCP，供全部生命周期复用。"""
        from core.events import event_registry, pipeline_engine
        from services.task.definitions import task_registry

        event_registry.unregister_owner(name)
        pipeline_engine.unregister_owner(name)
        task_registry.unregister_owner(name)
        self._unregister_mcp_tools_runtime(name)

    def _register_runtime_capabilities(self, name: str, instance) -> None:
        """校验并原子注册插件声明的全部运行时能力。"""
        from core.events import event_registry, pipeline_engine
        from services.task.definitions import task_registry

        definitions = instance.get_event_definitions() or []
        subscriptions = instance.get_event_subscriptions() or []
        pipelines = instance.get_pipeline_handlers() or []
        tasks = instance.get_task_definitions() or []
        if not all(isinstance(items, list) for items in (definitions, subscriptions, pipelines, tasks)):
            raise ValueError(f"插件 '{name}' 的能力声明必须返回列表")
        pages = instance.get_pages() or []
        if pages:
            self._validate_plugin_pages(name, pages)
            from core.plugin_page_validator import validate_plugin_page_sources
            plugin_root = self.plugins_dir / self._find_plugin_path(name)
            validate_plugin_page_sources(plugin_root, pages)
        self._unregister_runtime_capabilities(name)
        from core.platform.plugin_lifecycle import register_plugin_resources
        register_plugin_resources(name, self.load_metadata)
        try:
            for definition in definitions:
                if definition.owner != name:
                    raise ValueError(f"事件定义 owner 必须为插件名: {definition.name}")
                event_registry.register_definition(definition)
            for definition in tasks:
                if definition.owner != name:
                    raise ValueError(f"任务 owner 必须为插件名: {definition.name}")
                task_registry.register(definition)
            for subscription in subscriptions:
                if subscription.owner != name:
                    raise ValueError(f"事件订阅 owner 必须为插件名: {subscription.event_name}")
                event_registry.register_subscription(subscription)
            for declaration in pipelines:
                if declaration.owner != name:
                    raise ValueError(f"管道 owner 必须为插件名: {declaration.name}")
                pipeline_engine.register(declaration)
            self._register_mcp_tools_runtime(name, instance)
        except Exception:
            self._unregister_runtime_capabilities(name)
            raise

    def restore_capabilities(self, name: str) -> bool:
        """
        恢复插件显式能力（服务重启后或插件重新启用时调用）。
        """
        plugin_cls, meta = self._load_plugin_class(name)
        if not plugin_cls:
            return False
        instance = plugin_cls(None, meta.get("config", {}))
        try:
            self._register_runtime_capabilities(name, instance)
            self._loaded[name] = instance
            logger.info("插件 '%s' 运行时能力已恢复", name)
            return True
        except Exception:
            logger.exception("插件 '%s' 运行时能力恢复失败", name)
            self._unregister_runtime_capabilities(name)
            self._loaded.pop(name, None)
            return False

    @staticmethod
    def _register_mcp_tools_runtime(name: str, instance: "BasePlugin"):
        """
        注册插件声明的 MCP 工具（与其他显式能力在同一点位调用）

        先注销再注册保证幂等；MCP_ENABLED=False 时 registry 内部空操作，
        工具注册失败不阻断插件主流程（只记日志）。
        """
        try:
            from services.mcp.registry import register_tools, unregister_tools
            unregister_tools(name)
            register_tools(name, instance.get_mcp_tools())
        except Exception as e:
            logger.warning(f"插件 '{name}' MCP 工具注册失败(不影响插件运行): {e}", exc_info=True)

    @staticmethod
    def _unregister_mcp_tools_runtime(name: str):
        """注销插件的 MCP 工具（禁用/卸载时调用，失败不阻断主流程）"""
        try:
            from services.mcp.registry import unregister_tools
            unregister_tools(name)
        except Exception as e:
            logger.warning(f"插件 '{name}' MCP 工具注销失败: {e}", exc_info=True)

    async def install(self, name: str, db) -> bool:
        """
        安装插件（事务安全）

        流程:
        1. 动态加载插件类
        2. 调用 instance.install() 执行五步操作
        3. 写入 hy_plugin 表
        4. 原子注册 Event、Pipeline、Task 与 MCP 显式能力
        5. 注册权限树
        全程事务包裹，任一步骤失败自动回滚

        会话安全: install 事务内使用持 db 的实例；运行时能力与 _loaded
        改用 Plugin(None, config) 新实例，避免钩子 handler 长期持有
        安装期会话（请求结束即失效，后续触发必然报错）。
        """
        plugin_cls, meta = self._load_plugin_class(name)
        if not plugin_cls:
            return False
        instance = plugin_cls(db, meta.get("config", {}))
        module_name = meta.get("module", "") or self._find_plugin_path(name).split("/")[0]

        # === 事务开始 ===
        await db.begin()
        try:
            # 执行插件的 install() 方法
            success = await instance.install()
            if not success:
                await db.rollback()
                return False

            from core.db.plugin import PluginModel  # 写入插件注册表
            db.add(PluginModel(
                name=name, title=meta.get("title", name),
                version=meta.get("version", "1.0.0"), author=meta.get("author", ""),
                module=meta.get("module", module_name), status=1,
                config=json.dumps(meta.get("config", {})),
            ))
            # 运行时能力与驻留使用无会话新实例（会话安全，见方法注释）
            runtime_instance = plugin_cls(None, meta.get("config", {}))
            self._register_runtime_capabilities(name, runtime_instance)

            # 注册插件权限树（传入主事务会话，随 install 事务一起提交/回滚）
            perm_tree = instance.get_permissions()
            if perm_tree:
                from core.auth.rbac import register_plugin_permissions
                await register_plugin_permissions(name, perm_tree, db=db)

            # 注册插件页面到 hy_nav（页面注册层）
            await self._register_plugin_nav(name, instance, meta, db)

            # === 事务提交 ===
            await db.commit()

            self._loaded[name] = runtime_instance
            # 发布插件安装瞬时事件；异常不影响已提交事务。
            await emit_plugin_installed(name)
            return True

        except Exception as e:
            await db.rollback()
            # 回收事务内已写入全局注册表的运行时能力。
            self._unregister_runtime_capabilities(name)
            self._loaded.pop(name, None)
            logger.error(f"插件 '{name}' 安装失败(已回滚): {e}", exc_info=True)
            return False

    async def uninstall(self, name: str, db, router_manager=None) -> bool:
        """
        卸载插件

        流程（考虑 MySQL DDL 隐式提交）:
        1. 框架层先执行所有 DML 清理（菜单/导航/旧元数据/权限/插件记录）
        2. 再调用 instance.uninstall()（可能含 DDL，会隐式提交）
        3. 注销全部运行时能力

        参数:
            router_manager: 可选，传入时将插件加入路由禁用集合，
                已挂载路由卸载后即时 404（路由本身无法运行时移除）

        注意: MySQL 的 DROP TABLE 等 DDL 语句会触发隐式提交，
        因此不能用单一事务包裹整个卸载流程。
        框架层 DML 先执行并提交，确保即使插件 DDL 失败，
        菜单/权限/插件记录也已清理。
        """
        try:
            from core.platform.plugin_lifecycle import begin_uninstall
            await begin_uninstall(name, router_manager)
            instance = self._loaded.get(name)
            # 若插件不在 _loaded（如服务器重启后），临时实例化以执行卸载清理
            if not instance:
                plugin_cls, meta = self._load_plugin_class(name)
                if plugin_cls:
                    instance = plugin_cls(db, meta.get("config", {}))
                else:
                    logger.warning(f"临时实例化插件 '{name}' 失败，跳过插件自身卸载逻辑")

            # === 第一步：框架层 DML 清理（单一事务） ===
            try:
                from sqlalchemy import delete as sa_delete

                # 注销插件权限树
                from core.auth.rbac import unregister_plugin_permissions
                await unregister_plugin_permissions(name)

                # 清理插件菜单（框架层兜底）
                from core.db.menu import Menu
                from sqlalchemy import or_
                # 同时按 plugin 字段和路径匹配，清理导航管理手动添加的插件页面（plugin=''但path含插件名）
                await db.execute(
                    sa_delete(Menu).where(
                        or_(
                            Menu.plugin == name,
                            Menu.path.like(f"%/plugin/{name}/%"),
                        )
                    )
                )

                # 清理插件页面注册（hy_nav）
                from core.db.nav import Nav
                await db.execute(sa_delete(Nav).where(Nav.plugin == name))

                # 删除 hy_plugin 记录
                from core.db.plugin import PluginModel
                await db.execute(
                    delete(PluginModel).where(PluginModel.name == name)
                )

                await db.commit()

            except Exception as e:
                await db.rollback()
                logger.error(f"插件 '{name}' 框架层清理失败: {e}", exc_info=True)
                return False

            # === 第二步：插件自身卸载（可能含 DDL，会隐式提交） ===
            if instance:
                try:
                    await instance.uninstall()
                except Exception as e:
                    logger.warning(f"插件 '{name}' 自身卸载失败(框架层已清理): {e}", exc_info=True)

            # === 第三步：按 owner 清理全部运行时能力 ===
            self._unregister_runtime_capabilities(name)
            self._loaded.pop(name, None)
            from core.platform.plugin_lifecycle import finalize_lifecycle
            await finalize_lifecycle(name, "uninstalled")
            # 发布插件卸载瞬时事件。
            await emit_plugin_uninstalled(name)
            return True

        except Exception as e:
            logger.error(f"插件 '{name}' 卸载异常: {e}", exc_info=True)
            return False

    async def enable(self, name: str, db, router_manager=None) -> bool:
        """
        启用插件 — 更新 hy_plugin 表 status=1 + 运行时即时恢复

        参数:
            router_manager: 可选，传入时同步完成路由门禁放行与补注册，
                使启用后无需重启即时可用（端点层传 app.state.router_manager）
        """
        from core.db.plugin import PluginModel
        from sqlalchemy import select as sa_select, update
        # 幂等前置检查：已启用且运行时实例在位时直接短路成功，
        # 避免重复启用（双击/重放）反复重建实例与钩子
        record = (await db.execute(
            sa_select(PluginModel).where(PluginModel.name == name)
        )).scalar_one_or_none()
        if record and record.status == 1 and name in self._loaded:
            logger.info(f"插件 '{name}' 已处于启用状态，幂等返回")
            return True
        await db.execute(
            update(PluginModel).where(PluginModel.name == name).values(status=1)
        )
        await db.commit()
        # 运行时恢复：显式能力重新注册 + 路由门禁放行。
        if not self.restore_capabilities(name):
            await db.execute(update(PluginModel).where(PluginModel.name == name).values(status=2))
            await db.commit()
            return False
        from core.platform.plugin_lifecycle import finalize_lifecycle, resume_plugin_owner_or_disable
        if not await resume_plugin_owner_or_disable(name, db, self, PluginModel, update):
            return False
        runtime_instance = self._loaded.get(name)
        if runtime_instance:
            callback = getattr(runtime_instance, "on_runtime_enable", None)
            if callback:
                await callback()
        if router_manager:
            if not router_manager.is_registered(name):
                router_manager.register_plugin_router(name)
            router_manager.mark_enabled(name)
        await finalize_lifecycle(name, "enabled")
        return True

    async def disable(self, name: str, db, router_manager=None) -> bool:
        """
        禁用插件 — 更新 hy_plugin 表 status=2 + 运行时即时断开

        参数:
            router_manager: 可选，传入时将插件加入路由禁用集合，
                其已挂载路由即时返回 404（FastAPI 不支持运行时移除路由）
        """
        from core.db.plugin import PluginModel
        from sqlalchemy import update
        from core.platform.plugin_lifecycle import finalize_lifecycle, pause_plugin_owner
        if not await pause_plugin_owner(name, router_manager):
            return False
        await db.execute(
            update(PluginModel).where(PluginModel.name == name).values(status=2)
        )
        await db.commit()
        # 运行时断开：先允许插件释放长连接等资源，再注销运行时能力。
        runtime_instance = self._loaded.get(name)
        if runtime_instance:
            callback = getattr(runtime_instance, "on_runtime_disable", None)
            if callback:
                await callback()
        self._unregister_runtime_capabilities(name)
        self._loaded.pop(name, None)
        await finalize_lifecycle(name, "disabled")
        return True
