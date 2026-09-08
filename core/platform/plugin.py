"""插件更新、owner 生命周期和运行时快照门面。"""

import logging
from datetime import datetime, timedelta
from core.time_utils import china_now
from uuid import uuid4

from core.platform.audit import audit_log
from core.platform.event import publish_lifecycle_event
from core.platform.health import platform_health
from core.platform.lock import platform_lock
from core.platform.plugin_package import (
    cleanup_staged_package,
    inspect_plugin_package,
    stage_plugin_package,
    verify_staged_package,
)
from core.platform.plugin_update_store import (
    find_persisted_pending,
    load_persisted_plan,
    persist_plan,
    update_plan_in_session,
)
from core.platform.plugin_runtime import (
    installed_version,
    record_lifecycle,
    runtime_snapshot,
)
from core.plugin_manager import PluginManager


logger = logging.getLogger(__name__)


class PluginPlatform:
    """在现有 PluginManager 之上提供预检/确认/回滚协议。"""

    PLAN_TTL = timedelta(minutes=30)

    def __init__(self) -> None:
        self._plans: dict[str, dict] = {}
        self._last_failure: dict[str, str] = {}
        self._persisted_operations: set[str] = set()

    async def _load_persisted_plan(self, operation_id: str) -> dict | None:
        return await load_persisted_plan(self, operation_id)

    async def _persist_plan(self, plan: dict) -> bool:
        return await persist_plan(plan)

    async def _find_persisted_pending(
        self, plugin_id: str, target_version: str = "", statuses=("awaiting_restart",)
    ) -> dict | None:
        return await find_persisted_pending(self, plugin_id, target_version, statuses)

    async def get_update_status(self, plugin_name: str) -> dict | None:
        """返回插件当前有效的待重启计划；失败/回滚计划不影响正常升级提示。"""
        pending = await self._find_persisted_pending(plugin_name, statuses=("awaiting_restart",))
        if pending:
            self._plans[pending["operation_id"]] = pending
            return pending
        pending = next((item for item in self._plans.values() if (
            item.get("plugin_id") == plugin_name and item.get("status") == "awaiting_restart"
        )), None)
        return dict(pending) if pending else None

    async def _update_persisted_plan(self, plan: dict) -> None:
        self._plans[plan["operation_id"]] = dict(plan)
        if await self._persist_plan(plan):
            self._persisted_operations.add(plan["operation_id"])

    @staticmethod
    async def _update_plan_in_session(db, plan: dict) -> None:
        await update_plan_in_session(db, plan)

    def validate_local_package(self, plugin_id: str, package_ref: str) -> dict:
        """校验已暂存目录或 ZIP；空引用表示校验当前磁盘插件。"""
        manager = PluginManager()
        info = inspect_plugin_package(plugin_id, package_ref, manager)
        data = info["manifest"]
        return {
            "plugin_id": plugin_id,
            "version": info["version"],
            "module": info["module"],
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

    async def prepare_update(  # noqa: C901, PLR0912
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
        plan = None
        try:
            manager = PluginManager()
            package_info = inspect_plugin_package(plugin_id, package_ref, manager)
            manifest = package_info["manifest"]
            target = {
                "version": package_info["version"],
                "module": package_info["module"],
                "title": str(manifest.get("title") or plugin_id),
            }
            current = await self._installed_version(plugin_id)
            if not current:
                try:
                    current = str(manager.load_metadata(plugin_id).get("version") or "")
                except Exception:
                    current = ""
            if current and manager.compare_versions(target["version"], str(current)) <= 0:
                raise ValueError("目标版本不高于当前版本")
            lock_key = f"plugin-prepare:{plugin_id}"
            if not await platform_lock.acquire(lock_key, identity or "system"):
                raise RuntimeError("插件正在预检，请稍后重试")
            try:
                existing = await self._find_persisted_pending(
                    plugin_id, target["version"], statuses=("prepared", "awaiting_restart"),
                )
                existing = existing or next((item for item in self._plans.values() if (
                    item.get("plugin_id") == plugin_id
                    and item.get("target_version") == target["version"]
                    and item.get("status") in {"prepared", "awaiting_restart"}
                )), None)
                if existing and existing.get("status") == "prepared":
                    created_at = datetime.fromisoformat(existing["created_at"])
                    if china_now() - created_at > self.PLAN_TTL:
                        existing.update(status="failed", restart_required=False, error_reason="更新计划已过期")
                        await self._persist_plan(existing)
                        cleanup_staged_package(existing)
                        existing = None
                if existing:
                    self._plans[existing["operation_id"]] = existing
                    return dict(existing)
                staged_ref, package_digest = stage_plugin_package(plugin_id, operation_id, package_info)
                plan = {
                    "operation_id": operation_id, "plugin_id": plugin_id, "owner": plugin_id,
                    "current_version": str(current), "target_version": target["version"],
                    "package_ref": staged_ref, "package_digest": package_digest,
                    "package_module": target["module"], "status": "prepared",
                    "restart_required": False, "identity": identity,
                    "created_at": china_now().isoformat(), "task_id": None,
                }
                self._plans[operation_id] = plan
                await self._persist_plan(plan)
                self._persisted_operations.add(operation_id)
                if enqueue_task:
                    from core.platform.task import submit_platform_task

                    try:
                        plan["task_id"] = await submit_platform_task(
                            "plugin.update.prepare", {"plugin_id": plugin_id, "package_ref": staged_ref},
                            description=f"插件更新预检: {plugin_id}", idempotency_key=operation_id,
                            correlation_id=operation_id,
                        )
                        await self._persist_plan(plan)
                    except Exception as exc:
                        plan.update(
                            status="failed", restart_required=False,
                            error_reason=f"更新预检任务入队失败: {exc}",
                        )
                        await self._persist_plan(plan)
                        cleanup_staged_package(plan)
                        raise
            finally:
                platform_lock.release(lock_key, identity or "system")
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
            if plan:
                if operation_id in self._persisted_operations and plan.get("status") != "failed":
                    plan.update(status="failed", restart_required=False, error_reason=str(exc))
                    try:
                        await self._persist_plan(plan)
                    except RuntimeError:
                        logger.exception("插件预检失败状态持久化失败: operation_id=%s", operation_id)
                    else:
                        cleanup_staged_package(plan)
                elif operation_id not in self._persisted_operations:
                    cleanup_staged_package(plan)
                    self._plans.pop(operation_id, None)
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
        plan = await self._load_persisted_plan(operation_id) or self._plans.get(operation_id)
        if plan:
            self._plans[operation_id] = plan
        if not plan or plan.get("plugin_id") != plugin_id:
            await self._record_update_failure(
                plugin_id, operation_id, "confirm", "更新计划不存在", request=request,
            )
            raise ValueError("更新计划不存在")
        if plan.get("status") == "awaiting_restart" and operation_id in self._persisted_operations:
            await self._verify_confirm_package(plugin_id, operation_id, plan, request)
            return dict(plan)
        created_at = datetime.fromisoformat(plan["created_at"])
        if china_now() - created_at > self.PLAN_TTL:
            plan["status"] = "failed"
            await self._record_update_failure(
                plugin_id, operation_id, "confirm", "更新计划已过期", plan=plan, request=request,
            )
            raise ValueError("更新计划已过期")
        if plan.get("status") != "prepared":
            await self._record_update_failure(
                plugin_id, operation_id, "confirm",
                f"更新计划当前状态不可确认: {plan.get('status')}", request=request,
            )
            raise ValueError(f"更新计划当前状态不可确认: {plan.get('status')}")
        await self._verify_confirm_package(plugin_id, operation_id, plan, request)
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
                request=request,
            )
            raise RuntimeError("插件正在更新，请稍后重试")
        try:
            previous = dict(plan)
            plan["status"] = "awaiting_restart"
            plan["restart_required"] = True
            plan["confirmed_by"] = identity
            plan["confirmed_at"] = china_now().isoformat()
            try:
                await self._persist_plan(plan)
            except RuntimeError:
                self._plans[operation_id] = previous
                raise
            self._persisted_operations.add(operation_id)
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

    async def _verify_confirm_package(
        self, plugin_id: str, operation_id: str, plan: dict, request,
    ) -> None:
        """确认阶段摘要失败必须让计划失效，避免重复确认受污染快照。"""
        try:
            verify_staged_package(plan)
        except ValueError as exc:
            persisted = await self._record_update_failure(
                plugin_id, operation_id, "confirm", str(exc), plan=plan, request=request,
            )
            if not persisted:
                raise RuntimeError("插件更新失败状态持久化失败") from exc
            cleanup_staged_package(plan)
            raise

    async def _record_update_failure(
        self, plugin_id: str, operation_id: str, phase: str, reason: str,
        *, plan: dict | None = None, request=None,
    ) -> bool:
        """失败审计和可靠事件不能覆盖原始业务错误。"""
        plan = plan or {}
        persisted = True
        if plan:
            plan["status"] = "failed"
            plan["error_reason"] = reason
            try:
                await self._persist_plan(plan)
            except RuntimeError:
                persisted = False
                logger.exception("插件更新失败状态持久化失败: operation_id=%s", operation_id)
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
        return persisted

    async def rollback_update(self, plugin_id: str, operation_id: str, *, request=None) -> dict:
        """取消尚未应用的更新计划；已应用计划禁止代码单边回退。"""
        plan = self._plans.get(operation_id) or await self._load_persisted_plan(operation_id)
        if plan:
            self._plans[operation_id] = plan
        if not plan or plan.get("plugin_id") != plugin_id:
            raise ValueError("更新计划不存在")
        if plan.get("status") == "cancelled":
            return dict(plan)
        if plan.get("status") == "applied":
            raise ValueError("已应用的插件更新不支持无数据库降级迁移的回退")
        if plan.get("status") not in {"prepared", "awaiting_restart"}:
            raise ValueError(f"更新计划当前状态不可回退: {plan.get('status')}")
        previous = dict(plan)
        plan["status"] = "cancelled"
        plan["restart_required"] = False
        plan["error_reason"] = ""
        try:
            await self._persist_plan(plan)
        except RuntimeError:
            self._plans[operation_id] = previous
            raise
        cleanup_staged_package(plan)
        await audit_log(
            f"生成插件回滚计划: {plugin_id}", "plugin_update_failed",
            owner=plugin_id, version=plan.get("target_version", ""), operation_id=operation_id,
            phase="rollback", result="cancelled", request=request,
        )
        return dict(plan)

    async def runtime_snapshot(self, plugin_id: str, *, manager=None, router_manager=None) -> dict:
        return await runtime_snapshot(self, plugin_id, manager=manager, router_manager=router_manager)

    async def record_lifecycle(self, plugin_id: str, action: str, *, request=None) -> None:
        await record_lifecycle(plugin_id, action, request=request)

    @staticmethod
    async def _installed_version(plugin_id: str) -> str:
        return await installed_version(plugin_id)

plugin_platform = PluginPlatform()
