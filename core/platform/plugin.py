"""插件更新、owner 生命周期和运行时快照门面。"""

import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from core.platform.audit import audit_log
from core.platform.event import publish_lifecycle_event
from core.platform.health import platform_health
from core.platform.lock import platform_lock
from core.platform.resource import resource_registry
from core.plugin_manager import PluginManager


class PluginPlatform:
    """在现有 PluginManager 之上提供预检/确认/回滚协议。"""

    PLAN_TTL = timedelta(minutes=30)

    def __init__(self) -> None:
        self._plans: dict[str, dict] = {}
        self._last_failure: dict[str, str] = {}

    def validate_local_package(self, plugin_id: str, package_ref: str) -> dict:  # noqa: C901, PLR0912
        """校验已暂存目录或 ZIP；空引用表示校验当前磁盘插件。"""
        manager = PluginManager()
        if not plugin_id:
            raise ValueError("插件标识不能为空")
        if package_ref:
            path = Path(package_ref).resolve()
            if path.is_dir():
                manifest_path = path / "plugin.json"
                if not manifest_path.is_file():
                    raise ValueError("插件包目录缺少 plugin.json")
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            elif path.is_file() and path.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(path) as archive:
                        self._validate_zip_names(archive.namelist())
                        name = next((item for item in archive.namelist() if item.endswith("plugin.json")), "")
                        if not name:
                            raise ValueError("插件包缺少 plugin.json")
                        data = json.loads(archive.read(name).decode("utf-8"))
                except zipfile.BadZipFile as exc:
                    raise ValueError("插件 ZIP 文件无效") from exc
            else:
                raise ValueError("插件包必须是目录或 ZIP 文件")
        else:
            try:
                data = manager.load_metadata(plugin_id)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"插件 manifest 无法读取: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("插件 manifest 顶层必须是对象")
        declared = str(data.get("name") or "")
        if not declared:
            raise ValueError("插件 manifest 缺少 name")
        if declared != plugin_id:
            raise ValueError("插件包 name 与目标插件不一致")
        owner_id = str(data.get("owner_id") or data.get("owner") or plugin_id)
        if owner_id != plugin_id:
            raise ValueError("插件包 owner_id 与目标插件不一致")
        version = str(data.get("version") or "")
        if not version:
            raise ValueError("插件包缺少 version")
        for field in ("dependencies", "conflicts", "permissions", "capabilities", "pages", "resources", "tasks", "events"):
            if field in data and not isinstance(data[field], list):
                raise ValueError(f"插件 manifest 字段必须是数组: {field}")
        for field in ("dependencies", "conflicts"):
            for item in data.get(field, []):
                item_name = item if isinstance(item, str) else item.get("name", "") if isinstance(item, dict) else ""
                if not item_name or item_name == plugin_id:
                    raise ValueError(f"插件 manifest {field} 声明无效: {item!r}")
        compatible = data.get("compatible_app_versions", [])
        if not isinstance(compatible, list) or any(not isinstance(item, str) or not item.strip() for item in compatible):
            raise ValueError("插件 manifest compatible_app_versions 必须是非空字符串数组")
        if not isinstance(data.get("health", {}), dict):
            raise ValueError("插件 manifest health 必须是对象")
        for item in data.get("resources", []):
            path_value = item if isinstance(item, str) else item.get("path", "")
            parsed = urlsplit(str(path_value))
            if not path_value or parsed.scheme or parsed.netloc or str(path_value).startswith(("/", "\\")) or ".." in str(path_value).replace("\\", "/").split("/"):
                raise ValueError(f"插件资源必须是包内本地路径: {path_value}")
        manager.compare_versions(version, version)
        return {
            "plugin_id": plugin_id,
            "version": version,
            "module": str(data.get("module") or ""),
            "title": str(data.get("title") or plugin_id),
            "owner": plugin_id,
            "package_ref": str(package_ref or ""),
            "dependencies": list(data.get("dependencies", [])),
            "conflicts": list(data.get("conflicts", [])),
        }

    async def enable(self, plugin_id: str, manager, db, router_manager=None, request=None) -> dict:
        """通过既有 PluginManager 启用 owner，并返回统一运行时快照。"""
        if not await manager.enable(plugin_id, db, router_manager=router_manager):
            raise RuntimeError(f"插件 '{plugin_id}' 启用失败")
        await audit_log(
            f"启用插件: {plugin_id}", "plugin_enable", owner=plugin_id,
            phase="enable", request=request,
        )
        return await self.runtime_snapshot(plugin_id, manager=manager, router_manager=router_manager)

    async def disable(self, plugin_id: str, manager, db, router_manager=None, request=None) -> dict:
        """通过既有 PluginManager 禁用 owner；暂停失败时保留失败状态。"""
        if not await manager.disable(plugin_id, db, router_manager=router_manager):
            raise RuntimeError(f"插件 '{plugin_id}' 禁用失败")
        await audit_log(
            f"禁用插件: {plugin_id}", "plugin_disable", owner=plugin_id,
            phase="disable", request=request,
        )
        return await self.runtime_snapshot(plugin_id, manager=manager, router_manager=router_manager)

    async def uninstall(self, plugin_id: str, manager, db, router_manager=None, request=None) -> dict:
        """通过既有 PluginManager 卸载 owner，并确认任务取消成功。"""
        if not await manager.uninstall(plugin_id, db, router_manager=router_manager):
            raise RuntimeError(f"插件 '{plugin_id}' 卸载失败")
        await audit_log(
            f"卸载插件: {plugin_id}", "plugin_disable", owner=plugin_id,
            phase="uninstall", request=request,
        )
        return {"plugin_id": plugin_id, "owner_gate": "disabled", "status": "uninstalled"}

    async def prepare_update(
        self,
        plugin_id: str,
        package_ref: str = "",
        *,
        identity: str = "system",
        request=None,
        enqueue_task: bool = False,
    ) -> dict:
        """预检插件包并生成短期更新计划，不修改代码或数据库版本。"""
        operation_id = f"plugin-{uuid4().hex}"
        try:
            target = self.validate_local_package(plugin_id, package_ref)
            manager = PluginManager()
            current = await self._installed_version(plugin_id)
            if not current:
                try:
                    current = str(manager.load_metadata(plugin_id).get("version") or "")
                except Exception:
                    current = ""
            if current and manager.compare_versions(target["version"], str(current)) <= 0:
                raise ValueError("目标版本不高于当前版本")
            plan = {
                "operation_id": operation_id,
                "plugin_id": plugin_id,
                "owner": plugin_id,
                "current_version": str(current),
                "target_version": target["version"],
                "package_ref": target["package_ref"],
                "status": "prepared",
                "restart_required": False,
                "identity": identity,
                "created_at": datetime.now().isoformat(),
                "task_id": None,
            }
            if enqueue_task:
                from core.platform.task import submit_platform_task

                plan["task_id"] = await submit_platform_task(
                    "plugin.update.prepare", {
                        "plugin_id": plugin_id, "package_ref": package_ref,
                    },
                    description=f"插件更新预检: {plugin_id}",
                    idempotency_key=operation_id,
                    correlation_id=operation_id,
                )
            self._plans[operation_id] = plan
            platform_health.mark(f"plugin:{plugin_id}", "ready", version=target["version"])
            await audit_log(
                f"插件更新预检: {plugin_id}", "plugin_update_prepare",
                owner=plugin_id, version=target["version"], current_version=str(current), operation_id=operation_id,
                phase="prepare", request=request,
            )
            await publish_lifecycle_event("plugin.update.prepared", {
                "plugin_name": plugin_id, "version": target["version"],
                "operation_id": operation_id,
            }, correlation_id=operation_id)
            return dict(plan)
        except Exception as exc:
            self._last_failure[plugin_id] = str(exc)
            platform_health.mark(f"plugin:{plugin_id}", "failed", str(exc), operation_id=operation_id)
            await audit_log(
                f"插件更新预检失败: {plugin_id}", "plugin_update_failed",
                owner=plugin_id, operation_id=operation_id, phase="prepare",
                result="failed", error_reason=str(exc), request=request,
            )
            try:
                await publish_lifecycle_event("plugin.update.failed", {
                    "plugin_name": plugin_id, "version": "", "operation_id": operation_id,
                }, correlation_id=operation_id)
            except Exception:
                pass
            raise

    async def confirm_update(
        self, plugin_id: str, operation_id: str, *, identity: str = "system", request=None
    ) -> dict:
        """确认更新计划；只改变计划状态并要求重启，不替换文件。"""
        plan = self._plans.get(operation_id)
        if not plan or plan.get("plugin_id") != plugin_id:
            await self._record_update_failure(
                plugin_id, operation_id, "confirm", "更新计划不存在", request=request,
            )
            raise ValueError("更新计划不存在")
        created_at = datetime.fromisoformat(plan["created_at"])
        if datetime.now() - created_at > self.PLAN_TTL:
            plan["status"] = "failed"
            await self._record_update_failure(
                plugin_id, operation_id, "confirm", "更新计划已过期", plan=plan, request=request,
            )
            raise ValueError("更新计划已过期")
        if plan.get("status") != "prepared":
            await self._record_update_failure(
                plugin_id, operation_id, "confirm",
                f"更新计划当前状态不可确认: {plan.get('status')}", plan=plan, request=request,
            )
            raise ValueError(f"更新计划当前状态不可确认: {plan.get('status')}")
        current_version = await self._installed_version(plugin_id)
        if current_version and str(current_version) != str(plan.get("current_version") or ""):
            plan["status"] = "failed"
            await self._record_update_failure(
                plugin_id, operation_id, "confirm", "更新计划对应的当前版本已变化，请重新预检",
                plan=plan, request=request,
            )
            raise ValueError("更新计划对应的当前版本已变化，请重新预检")
        lock_key = f"plugin:{plugin_id}"
        if not await platform_lock.acquire(lock_key, identity or "system"):
            await self._record_update_failure(
                plugin_id, operation_id, "confirm", "插件正在更新，请稍后重试",
                plan=plan, request=request,
            )
            raise RuntimeError("插件正在更新，请稍后重试")
        try:
            plan["status"] = "awaiting_restart"
            plan["restart_required"] = True
            plan["confirmed_by"] = identity
            plan["confirmed_at"] = datetime.now().isoformat()
            await audit_log(
                f"确认插件更新: {plugin_id}", "plugin_update_confirm",
                owner=plugin_id, version=plan["target_version"], current_version=plan.get("current_version", ""), operation_id=operation_id,
                phase="confirm", request=request,
            )
            await publish_lifecycle_event("plugin.update.confirmed", {
                "plugin_name": plugin_id, "version": plan["target_version"],
                "operation_id": operation_id,
            }, correlation_id=operation_id)
            return dict(plan)
        finally:
            platform_lock.release(lock_key, identity or "system")

    async def _record_update_failure(
        self, plugin_id: str, operation_id: str, phase: str, reason: str,
        *, plan: dict | None = None, request=None,
    ) -> None:
        """失败审计和可靠事件不能覆盖原始业务错误。"""
        plan = plan or {}
        self._last_failure[plugin_id] = reason
        platform_health.mark(
            f"plugin:{plugin_id}", "failed", reason,
            operation_id=operation_id,
        )
        try:
            await audit_log(
                f"插件更新失败: {plugin_id}", "plugin_update_failed",
                owner=plugin_id, version=str(plan.get("target_version") or ""),
                current_version=str(plan.get("current_version") or ""),
                operation_id=operation_id, phase=phase, result="failed",
                error_reason=reason, request=request,
            )
        except Exception:
            pass
        try:
            await publish_lifecycle_event(
                "plugin.update.failed",
                {"plugin_name": plugin_id, "version": str(plan.get("target_version") or ""),
                 "operation_id": operation_id},
                correlation_id=operation_id,
            )
        except Exception:
            pass

    async def rollback_update(self, plugin_id: str, operation_id: str, *, request=None) -> dict:
        """生成回滚计划；实际文件和迁移回滚由未来停机 CLI 执行。"""
        plan = self._plans.get(operation_id)
        if not plan or plan.get("plugin_id") != plugin_id:
            raise ValueError("更新计划不存在")
        was_awaiting_restart = plan.get("status") == "awaiting_restart"
        plan["status"] = "rolled_back"
        plan["restart_required"] = was_awaiting_restart
        await audit_log(
            f"生成插件回滚计划: {plugin_id}", "plugin_update_failed",
            owner=plugin_id, version=plan.get("target_version", ""), operation_id=operation_id,
            phase="rollback", result="rolled_back", request=request,
        )
        return dict(plan)

    async def runtime_snapshot(self, plugin_id: str, *, manager=None, router_manager=None) -> dict:
        """返回插件运行状态，不加载或替换插件 Python 实现。"""
        manager = manager or PluginManager()
        try:
            metadata = manager.load_metadata(plugin_id)
        except Exception:
            metadata = {}
        registered = bool(router_manager and router_manager.is_registered(plugin_id))
        disabled = bool(router_manager and plugin_id in getattr(router_manager, "_disabled", set()))
        plan = next((item for item in self._plans.values() if item["plugin_id"] == plugin_id), None)
        resource_count = len(resource_registry.snapshot(plugin_id))
        return {
            "plugin_id": plugin_id,
            "owner": plugin_id,
            "version": str(metadata.get("version") or ""),
            "owner_gate": "disabled" if disabled else "enabled",
            "routes_registered": registered,
            "pages": len(self._declared_pages(manager, plugin_id)),
            "resources": resource_count,
            "tasks": await self._count_owner_tasks(plugin_id),
            "events": self._count_owner_events(plugin_id),
            "health": platform_health.snapshot().get(f"plugin:{plugin_id}", {"status": "unknown"}),
            "last_failure": self._last_failure.get(plugin_id, ""),
            "operation_id": plan.get("operation_id", "") if plan else "",
        }

    async def record_lifecycle(self, plugin_id: str, action: str, *, request=None) -> None:
        """记录启停生命周期审计和事件。"""
        if action not in {"enabled", "disabled"}:
            raise ValueError(f"不支持的插件生命周期动作: {action}")
        event_name = f"plugin.{action}"
        log_type = f"plugin_{action}"
        await audit_log(f"插件{action}: {plugin_id}", log_type, owner=plugin_id, phase=action, request=request)
        await publish_lifecycle_event(event_name, {"plugin_name": plugin_id}, durable=False)

    @staticmethod
    async def _installed_version(plugin_id: str) -> str:
        """读取已安装版本；数据库不可用时由调用方回退到磁盘 manifest。"""
        from sqlalchemy import select

        from core.db.base import async_session_factory
        from core.db.plugin import PluginModel

        try:
            async with async_session_factory() as db:
                record = (await db.execute(
                    select(PluginModel.version).where(PluginModel.name == plugin_id)
                )).scalar_one_or_none()
                return str(record or "")
        except Exception:
            return ""

    @staticmethod
    def _declared_pages(manager: PluginManager, plugin_id: str) -> list[dict]:
        plugin_cls, meta = manager._load_plugin_class(plugin_id)
        if not plugin_cls:
            return []
        try:
            return list(plugin_cls(None, meta.get("config", {})).get_pages() or [])
        except Exception:
            return []

    @staticmethod
    async def _count_owner_tasks(owner: str) -> int:
        from sqlalchemy import func, select
        from core.db.base import async_session_factory
        from core.db.task_queue import TaskQueue

        try:
            async with async_session_factory() as db:
                return int(await db.scalar(select(func.count(TaskQueue.id)).where(TaskQueue.owner == owner)) or 0)
        except Exception:
            return 0

    @staticmethod
    def _count_owner_events(owner: str) -> int:
        from core.events import event_registry

        return sum(1 for item in event_registry.subscription_items() if item.owner == owner)

    @staticmethod
    def _validate_zip_names(names: list[str]) -> None:
        for name in names:
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError(f"插件包路径越界: {name}")


plugin_platform = PluginPlatform()
