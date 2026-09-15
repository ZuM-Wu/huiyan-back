"""
插件管理器 — 慧眼护农 3.4.1 核心骨架

BasePlugin: 插件抽象基类，install() 必须完成五步操作
PluginManager: 插件发现、安装、卸载、启用、禁用、升级
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List

from core.config import BASE_DIR, settings
from core.hook_events import emit_plugin_installed
from core.plugin_base import BasePlugin
from core.plugin_loader import PLUGIN_MODULES, PluginLoaderMixin
from core.plugin_runtime import PluginRuntimeMixin
from core.plugin_upgrade import PluginUpgradeMixin

logger = logging.getLogger(__name__)

_VERSION_PATTERN = re.compile(
    r"^(?P<core>\d+(?:\.\d+)*)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_MIGRATION_PATTERN = re.compile(r"^upgrade_(?P<old>[^_]+)_(?P<new>[^_]+)\.sql$")
_VALID_NAV_TYPES = frozenset({"admin", "frontend"})
_REMOVED_PLUGIN_NAMES: frozenset[str] = frozenset({"sms_idcsmart"})


class _RepairDatabaseProxy:
    """限制修复钩子只能访问 manifest 声明的当前插件表。"""

    _TABLE_RE = re.compile(
        r"\b(?:from|join|into|update|table)\s+[`]?([A-Za-z_][A-Za-z0-9_]*)[`]?",
        re.IGNORECASE,
    )

    def __init__(self, session, allowed_tables: set[str]):
        self._session = session
        self._allowed_tables = allowed_tables

    async def execute(self, statement, *args, **kwargs):
        sql = str(statement)
        for table in self._TABLE_RE.findall(sql):
            if table.lower() not in {item.lower() for item in self._allowed_tables}:
                raise PermissionError(f"修复钩子越权访问表: {table}")
        return await self._session.execute(statement, *args, **kwargs)

    async def scalar(self, statement, *args, **kwargs):
        result = await self.execute(statement, *args, **kwargs)
        return result.scalar()

    async def scalars(self, statement, *args, **kwargs):
        result = await self.execute(statement, *args, **kwargs)
        return result.scalars()

    async def get(self, entity, ident, *args, **kwargs):
        table = getattr(getattr(entity, "__table__", None), "name", "")
        if table and table.lower() not in {item.lower() for item in self._allowed_tables}:
            raise PermissionError(f"修复钩子越权访问表: {table}")
        return await self._session.get(entity, ident, *args, **kwargs)

    def _check_model(self, instance) -> None:
        """拦截 ORM 写入，避免钩子借由 Session.add 绕过 SQL 表名校验。"""
        table = getattr(getattr(instance, "__table__", None), "name", "")
        if table and table.lower() not in {item.lower() for item in self._allowed_tables}:
            raise PermissionError(f"修复钩子越权访问表: {table}")

    def add(self, instance, _warn=False):
        self._check_model(instance)
        return self._session.add(instance, _warn=_warn)

    def add_all(self, instances):
        for instance in instances:
            self._check_model(instance)
        return self._session.add_all(instances)

    def delete(self, instance):
        self._check_model(instance)
        return self._session.delete(instance)

    async def merge(self, instance, *args, **kwargs):
        """拦截 ORM merge，避免通过批量合并绕过自有表边界。"""
        self._check_model(instance)
        return await self._session.merge(instance, *args, **kwargs)

    async def flush(self, *args, **kwargs):
        return await self._session.flush(*args, **kwargs)

    async def commit(self):
        raise RuntimeError("修复钩子不得自行 commit")

    def __getattr__(self, item):
        return getattr(self._session, item)


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


_COMPATIBILITY_RE = re.compile(
    r"^(?P<operator>>=|<=|==|=|>|<)?\s*(?P<version>.+?)\s*$"
)


def is_app_version_compatible(
    constraints: object, app_version: str | None = None
) -> bool:
    """判断插件声明的版本范围是否覆盖当前应用版本。"""
    if not isinstance(constraints, list) or not constraints:
        return False
    current = app_version or settings.app_version
    for raw_constraint in constraints:
        if not isinstance(raw_constraint, str):
            continue
        match = _COMPATIBILITY_RE.fullmatch(raw_constraint.strip())
        if not match:
            continue
        try:
            comparison = compare_versions(current, match.group("version"))
        except ValueError:
            continue
        operator = match.group("operator") or "=="
        if (
            (operator in {"=", "=="} and comparison == 0)
            or (operator == ">=" and comparison >= 0)
            or (operator == ">" and comparison > 0)
            or (operator == "<=" and comparison <= 0)
            or (operator == "<" and comparison < 0)
        ):
            return True
    return False


class PluginManager(PluginUpgradeMixin, PluginRuntimeMixin, PluginLoaderMixin):
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

    @staticmethod
    def is_app_version_compatible(constraints: object) -> bool:
        """判断插件 manifest 是否兼容当前应用版本。"""
        return is_app_version_compatible(constraints, settings.app_version)

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
            for plugin_dir in sorted(type_dir.iterdir(), key=lambda path: path.name.casefold()):
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
            await self._sync_mcp_tool_policies(name, db)

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
        """通过统一编排清理插件私有数据、框架记录与运行时能力。"""
        from core.plugin_uninstall import _uninstall_plugin

        return await _uninstall_plugin(self, name, db, router_manager)

    async def repair_database(self, name: str, report: dict, db) -> dict | bool:
        """在控制中心停机事务中调用插件自身的幂等数据库修复钩子。"""
        if hasattr(report, "execute") and isinstance(db, dict):
            report, db = db, report
        plugin_cls, meta = self._load_plugin_class(name)
        if not plugin_cls:
            return False
        declared_schema = meta.get("database_schema") or {"tables": []}
        allowed_tables = {
            str(item.get("name")) for item in declared_schema.get("tables", [])
            if isinstance(item, dict) and item.get("name")
        }
        instance = plugin_cls(_RepairDatabaseProxy(db, allowed_tables), meta.get("config", {}))
        callback = getattr(instance, "repair_database", None)
        from core.plugin_base import BasePlugin
        if callback is None or plugin_cls.repair_database is BasePlugin.repair_database:
            return False
        result = callback(report)
        if asyncio.iscoroutine(result):
            result = await result
        if result is True:
            return True
        return isinstance(result, dict) and result.get("status") == "repaired"

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
            await self._sync_mcp_tool_policies(name, db)
            await db.commit()
            logger.info(f"插件 '{name}' 已处于启用状态，幂等返回")
            return True
        await db.execute(
            update(PluginModel).where(PluginModel.name == name).values(status=1)
        )
        # 运行时恢复：显式能力重新注册 + 路由门禁放行。
        if not self.restore_capabilities(name):
            await db.rollback()
            return False
        await self._sync_mcp_tool_policies(name, db)
        await db.commit()
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
        from core.platform.plugin_lifecycle import (
            finalize_lifecycle,
            pause_plugin_owner,
            restore_plugin_owner_after_failed_disable,
        )
        if not await pause_plugin_owner(name, router_manager):
            return False
        try:
            await db.execute(
                update(PluginModel).where(PluginModel.name == name).values(status=2)
            )
            await db.commit()
        except Exception:
            logger.exception("插件 '%s' 禁用状态提交失败，开始回滚运行时门禁", name)
            try:
                await db.rollback()
            except Exception:
                logger.exception("插件 '%s' 禁用失败后数据库回滚异常", name)
            await restore_plugin_owner_after_failed_disable(name, router_manager)
            return False
        # 状态已提交：后续清理尽力执行，单项失败不能反向污染停用结果。
        runtime_instance = self._loaded.get(name)
        if runtime_instance:
            callback = getattr(runtime_instance, "on_runtime_disable", None)
            if callback:
                try:
                    await callback()
                except Exception:
                    logger.exception("插件 '%s' 运行时停用钩子失败，继续注销能力", name)
        failures = self._unregister_runtime_capabilities(name, strict=False)
        self._loaded.pop(name, None)
        try:
            from core.platform.resource import resource_registry
            resource_registry.invalidate_owner(name)
        except Exception:
            logger.exception("插件 '%s' 资源索引注销失败", name)
            failures.append("resource")
        try:
            await finalize_lifecycle(name, "disabled")
        except Exception:
            logger.exception("插件 '%s' 停用生命周期写入失败", name)
            failures.append("lifecycle")
        if failures:
            logger.warning(
                "插件 '%s' 已停用，但部分运行时清理失败: %s",
                name,
                ", ".join(failures),
            )
        return True
