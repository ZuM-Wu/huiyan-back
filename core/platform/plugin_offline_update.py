"""插件停机更新执行服务。"""

from __future__ import annotations

import shutil
import socket
import json
import asyncio
from core.time_utils import china_now
from pathlib import Path

from sqlalchemy import select

from core.config import BASE_DIR
from core.db.base import async_session_factory
from core.db.plugin_update_plan import PluginUpdatePlanModel
from core.platform.plugin_package import (
    cleanup_staged_package,
    extract_staged_package,
    verify_staged_package,
)
from core.platform.plugin_runtime import installed_version
from core.platform.plugin_update_store import persist_plan, plan_to_dict
from core.plugin_manager import PluginManager


WORK_DIR = Path(BASE_DIR) / "runtime" / "plugin-update-work"
BACKUP_DIR = Path(BASE_DIR) / "runtime" / "plugin-update-backups"


def backend_is_listening(host: str = "127.0.0.1", port: int = 8000) -> bool:
    """确认 Web 服务端口是否正在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex((host, port)) == 0


async def load_update_plans(operation_id: str | None = None, operation_ids: list[str] | None = None) -> list[dict]:
    """读取指定计划，或读取全部可执行计划。"""
    async with async_session_factory() as db:
        query = select(PluginUpdatePlanModel)
        if operation_ids:
            query = query.where(PluginUpdatePlanModel.operation_id.in_(operation_ids))
        elif operation_id:
            query = query.where(PluginUpdatePlanModel.operation_id == operation_id)
        else:
            query = query.where(PluginUpdatePlanModel.status.in_(("awaiting_restart", "applying")))
        rows = (await db.execute(query.order_by(PluginUpdatePlanModel.created_at))).scalars().all()
    if (operation_id or operation_ids) and not rows:
        raise ValueError("插件更新计划不存在")
    return [plan_to_dict(row) for row in rows]


def _paths(plan: dict, manager: PluginManager) -> tuple[Path, Path, Path, Path]:
    operation_id = str(plan["operation_id"])
    plugin_name = str(plan["plugin_id"])
    module = str(plan.get("package_module") or "")
    if not module or any(value in {"", ".", ".."} for value in (operation_id, plugin_name, module)):
        raise ValueError("插件更新计划路径字段无效")
    live = manager.plugins_dir / module / plugin_name
    work_root = WORK_DIR / operation_id
    staged = work_root / module / plugin_name
    backup = BACKUP_DIR / operation_id / module / plugin_name
    return live, work_root, staged, backup


def _remove_runtime_tree(path: Path, root: Path) -> None:
    """仅删除已知 runtime 子目录，避免错误路径扩大删除范围。"""
    resolved, allowed = path.resolve(), root.resolve()
    if resolved == allowed or allowed not in resolved.parents:
        raise RuntimeError(f"拒绝清理非预期目录: {path}")
    shutil.rmtree(path, ignore_errors=True)


def _sort_plans_by_dependencies(plans: list[dict], manager: PluginManager) -> list[dict]:
    """按 manifest.dependencies 对批量计划做稳定拓扑排序。"""
    by_name = {str(plan.get("plugin_id") or plan.get("plugin_name")): plan for plan in plans}
    dependencies: dict[str, set[str]] = {}
    for name in by_name:
        try:
            metadata = manager.load_metadata(name)
        except Exception:
            metadata = {}
        values = set()
        for item in metadata.get("dependencies", []) if isinstance(metadata, dict) else []:
            dependency = item.get("name") if isinstance(item, dict) else item
            if dependency in by_name and dependency != name:
                values.add(str(dependency))
        dependencies[name] = values
    ordered: list[dict] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            # 循环依赖由安装/预检阶段负责报错；这里保持输入顺序避免死循环。
            return
        visiting.add(name)
        for dependency in sorted(dependencies.get(name, ())):
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(by_name[name])

    for name in by_name:
        visit(name)
    return ordered


def _prepare_staged_tree(plan: dict, manager: PluginManager) -> tuple[Path, Path, Path]:
    live, work_root, staged, backup = _paths(plan, manager)
    if work_root.exists():
        _remove_runtime_tree(work_root, WORK_DIR)
    extract_staged_package(plan, staged)
    validation = PluginManager(str(work_root))
    metadata = validation.load_metadata(str(plan["plugin_id"]))
    if str(metadata.get("version") or "") != str(plan["target_version"]):
        raise ValueError("解包后的插件版本与更新计划不一致")
    validation._migration_steps(
        str(plan["plugin_id"]), str(plan["current_version"]), str(plan["target_version"]),
    )
    return live, staged, backup


async def _mark_applied(plan: dict) -> dict:
    plan.update(
        status="applied", restart_required=False, error_reason="",
        applied_at=china_now().isoformat(),
    )
    await persist_plan(plan)
    cleanup_staged_package(plan)
    return dict(plan)


async def apply_update_plan(plan: dict, *, dry_run: bool = False) -> dict:  # noqa: C901, PLR0912
    """校验并应用单个停机更新计划。"""
    if plan.get("status") not in {"awaiting_restart", "applying"}:
        raise ValueError(f"更新计划当前状态不可执行: {plan.get('status')}")
    if plan.get("operation_type", "upgrade") == "repair":
        return await apply_repair_plan(plan, dry_run=dry_run)
    manager = PluginManager()
    live, work_root, _staged_path, backup = _paths(plan, manager)
    swapped = False
    if plan.get("status") == "applying" and live.is_dir() and backup.exists():
        try:
            swapped = str(manager.load_metadata(str(plan["plugin_id"])).get("version") or "") == str(
                plan["target_version"]
            )
        except (OSError, ValueError):
            swapped = False
    try:
        database_version = await installed_version(str(plan["plugin_id"]))
        if database_version == str(plan["target_version"]):
            if not live.is_dir() or str(manager.load_metadata(str(plan["plugin_id"])).get("version") or "") != database_version:
                raise RuntimeError("数据库已升级但插件代码未处于目标版本，需要人工恢复")
            if not dry_run:
                result = await _mark_applied(plan)
                backup_root = BACKUP_DIR / str(plan["operation_id"])
                if backup_root.exists():
                    _remove_runtime_tree(backup_root, BACKUP_DIR)
                return result
        verify_staged_package(plan)
        live, staged, backup = _prepare_staged_tree(plan, manager)
        if database_version == str(plan["target_version"]):
            return {**plan, "status": "validated"}
        if database_version != str(plan["current_version"]):
            raise ValueError("插件当前数据库版本与更新计划不一致")
        live_version = str(manager.load_metadata(str(plan["plugin_id"])).get("version") or "")
        already_swapped = live_version == str(plan["target_version"]) and plan.get("status") == "applying"
        allowed_live_versions = {str(plan["current_version"]), str(plan["target_version"])}
        if not already_swapped and live_version not in allowed_live_versions:
            raise ValueError("插件当前代码版本与更新计划不一致")
        if dry_run:
            return {**plan, "status": "validated", "restart_required": True}

        plan.update(status="applying", restart_required=True, error_reason="")
        await persist_plan(plan)
        swapped = swapped or already_swapped
        if not swapped:
            if backup.exists():
                raise RuntimeError("插件更新备份目录已存在，拒绝覆盖")
            backup.parent.mkdir(parents=True, exist_ok=True)
            live.replace(backup)
            try:
                staged.replace(live)
                swapped = True
            except Exception:
                backup.replace(live)
                raise
        async with async_session_factory() as db:
            upgraded = await manager.upgrade(str(plan["plugin_id"]), db, router_manager=None)
        database_version = await installed_version(str(plan["plugin_id"]))
        if not upgraded and database_version != str(plan["target_version"]):
            error = manager.get_upgrade_error(str(plan["plugin_id"]))
            raise RuntimeError(error.message if error else "插件迁移失败")
        result = await _mark_applied(plan)
        if backup.exists():
            _remove_runtime_tree(BACKUP_DIR / str(plan["operation_id"]), BACKUP_DIR)
        return result
    except Exception as exc:
        database_version = await installed_version(str(plan["plugin_id"]))
        if not dry_run and database_version == str(plan["target_version"]):
            return await _mark_applied(plan)
        if not dry_run and swapped and backup.exists():
            if live.exists():
                _remove_runtime_tree(live, manager.plugins_dir)
            backup.replace(live)
        if not dry_run and plan.get("status") in {"awaiting_restart", "applying"}:
            plan.update(status="failed", restart_required=False, error_reason=str(exc))
            await persist_plan(plan)
            cleanup_staged_package(plan)
            backup_root = BACKUP_DIR / str(plan["operation_id"])
            if swapped and backup_root.exists():
                _remove_runtime_tree(backup_root, BACKUP_DIR)
        raise
    finally:
        if work_root.exists():
            _remove_runtime_tree(work_root, WORK_DIR)


async def apply_repair_plan(plan: dict, *, dry_run: bool = False) -> dict:
    """停机执行单插件修复钩子，并在同一事务中复测结构。"""
    if plan.get("status") not in {"awaiting_restart", "applying"}:
        raise ValueError(f"修复计划当前不可执行: {plan.get('status')}")
    manager = PluginManager()
    plugin_name = str(plan.get("plugin_id") or "")
    if not plugin_name:
        raise ValueError("修复计划缺少插件标识")
    if dry_run:
        return {**plan, "status": "validated", "restart_required": True}
    try:
        async with async_session_factory() as db:
            report = json.loads(plan.get("diagnostics_json") or "{}")
            repaired = await asyncio.wait_for(
                manager.repair_database(plugin_name, report, db), timeout=300,
            )
            if not repaired:
                raise RuntimeError("插件修复钩子返回失败或未实现")
            from core.plugin_database_service import PluginDatabaseService
            meta = manager.load_metadata(plugin_name)
            schema = meta.get("database_schema") or {"version": 1, "tables": []}
            checked = await PluginDatabaseService(manager).detect_schema(db, schema)
            if checked.get("status") == "mismatch":
                raise RuntimeError("修复后数据库结构仍不完整")
            from core.db.plugin_database_state import PluginDatabaseStateModel
            from sqlalchemy import update
            await db.execute(update(PluginDatabaseStateModel).where(
                PluginDatabaseStateModel.plugin_name == plugin_name
            ).values(
                status="current", schema_status="current", report_json=json.dumps(checked, ensure_ascii=False),
                pending_operation_id="", last_repair_at=china_now(), last_success_at=china_now(), error_reason="",
            ))
            await db.commit()
        plan.update(status="applied", restart_required=False, error_reason="", applied_at=china_now().isoformat())
        await persist_plan(plan)
        try:
            from core.platform.audit import audit_log
            await audit_log(
                f"插件数据库修复完成: {plugin_name}", "plugin_database_repair",
                owner=plugin_name, operation_id=plan.get("operation_id", ""), phase="apply", result="success",
            )
        except Exception:
            pass
        return dict(plan)
    except Exception as exc:
        plan.update(status="failed", restart_required=False, error_reason=str(exc))
        await persist_plan(plan)
        # 修复事务已回滚；清理状态快照中的待处理编号并保留失败原因，
        # 使页面不会继续把已失败计划展示为可执行。
        try:
            from sqlalchemy import update
            from core.db.plugin_database_state import PluginDatabaseStateModel
            async with async_session_factory() as db:
                await db.execute(update(PluginDatabaseStateModel).where(
                    PluginDatabaseStateModel.plugin_name == plugin_name
                ).values(
                    pending_operation_id="", last_failure_at=china_now(),
                    error_reason=str(exc),
                ))
                await db.commit()
        except Exception:
            # 状态表不可用时不能覆盖原始修复异常，只记录调试信息。
            import logging
            logging.getLogger(__name__).debug(
                "修复失败状态回写失败: %s", plugin_name, exc_info=True,
            )
        raise


async def apply_selected_updates(
    *, operation_id: str | None = None, operation_ids: list[str] | None = None,
    apply_all: bool = False, dry_run: bool = False,
) -> list[dict]:
    """执行命令入口；服务监听时无条件拒绝文件和数据库变更。"""
    selected = [item for item in (operation_ids or []) if item]
    if selected and (operation_id or apply_all):
        raise ValueError("operation_id、operation_ids 与 apply_all 必须单独使用")
    if not selected and bool(operation_id) == bool(apply_all):
        raise ValueError("--operation-id 与 --all 必须二选一")
    if backend_is_listening():
        raise RuntimeError("检测到 127.0.0.1:8000 正在监听，请先完全停止后端")
    plans = await load_update_plans(operation_id, selected or None)
    plans = _sort_plans_by_dependencies(plans, PluginManager()) if len(plans) > 1 else plans
    continue_on_error = apply_all or len(plans) > 1
    results: list[dict] = []
    for plan in plans:
        try:
            results.append(await apply_update_plan(plan, dry_run=dry_run))
        except Exception as exc:
            if not continue_on_error:
                raise
            results.append({
                "operation_id": plan.get("operation_id", ""),
                "plugin_id": plan.get("plugin_id", ""),
                "status": "failed", "error_reason": str(exc),
            })
    return results
