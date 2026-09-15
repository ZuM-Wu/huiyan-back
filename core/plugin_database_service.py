"""平台级插件数据库体检服务。

该模块是跨插件数据库检查的唯一入口。插件只声明自身结构并实现可选的
repair_database 钩子，服务本身负责读取 information_schema 和保存快照。
"""

from __future__ import annotations

import hashlib
import importlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text

from core.db.base import async_session_factory
from core.db.plugin import PluginModel
from core.db.plugin_database_state import PluginDatabaseStateModel
from core.plugin_database_schema import (
    STATUS_PRIORITY, report_json, resolve_status, result_all, result_scalar,
    row_value, schema_digest, state_dict, validate_database_schema,
)
from core.plugin_manager import PLUGIN_MODULES, PluginManager
from core.time_utils import china_now

logger = logging.getLogger(__name__)
_CORE_PLUGIN_NAMES = frozenset({"system"})


class PluginDatabaseService:
    """扫描、状态转换和修复计划生成门面。"""

    _active_task_id: str | None = None
    _last_scan_at: datetime | None = None
    _last_scan_failure: dict | None = None

    def __init__(self, manager: PluginManager | None = None):
        self.manager = manager or PluginManager()

    @staticmethod
    def _plugin_root(manager: PluginManager, name: str) -> Any:
        return manager.plugins_dir / manager._find_plugin_path(name)

    def _load_manifest(self, name: str) -> tuple[dict, Any]:
        meta = self.manager.load_metadata(name)
        schema = validate_database_schema(meta.get("database_schema"))
        return meta, schema

    def _repair_supported(self, name: str, meta: dict) -> bool:
        try:
            module_name = self.manager._find_plugin_path(name).split("/")[0]
            module = importlib.import_module(f"plugins.{module_name}.{name}.plugin")
            plugin_cls = getattr(module, "Plugin", None)
            from core.plugin_base import BasePlugin
            return bool(plugin_cls and plugin_cls.repair_database is not BasePlugin.repair_database)
        except Exception:
            return False

    async def detect_schema(self, db, schema: dict) -> dict:
        """读取 MySQL information_schema，返回缺失表、列和索引。"""
        schema = validate_database_schema(schema)
        if not schema["tables"]:
            return {"status": "not_applicable", "missing_tables": [], "missing_columns": [], "missing_indexes": []}
        missing_tables: list[str] = []
        missing_columns: dict[str, list[str]] = {}
        missing_indexes: dict[str, list[dict]] = {}
        for table in schema["tables"]:
            table_name = table["name"]
            result = await db.execute(text("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name"), {"name": table_name})
            if not bool(await result_scalar(result)):
                missing_tables.append(table_name)
                continue
            result = await db.execute(text("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name"), {"name": table_name})
            actual_columns = {
                row_value(row, 0, "COLUMN_NAME", "column_name")
                for row in await result_all(result)
            }
            actual_columns.discard(None)
            absent_columns = [column for column in table["columns"] if column not in actual_columns]
            if absent_columns:
                missing_columns[table_name] = absent_columns
            result = await db.execute(text("SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name ORDER BY INDEX_NAME, SEQ_IN_INDEX"), {"name": table_name})
            actual_indexes: dict[str, dict] = {}
            for row in await result_all(result):
                raw_non_unique = row_value(row, 3, "NON_UNIQUE", "non_unique")
                if isinstance(raw_non_unique, str):
                    try:
                        raw_non_unique = int(raw_non_unique)
                    except ValueError:
                        raw_non_unique = 1
                values = (
                    row_value(row, 0, "INDEX_NAME", "index_name"),
                    row_value(row, 1, "COLUMN_NAME", "column_name"),
                    row_value(row, 2, "SEQ_IN_INDEX", "sequence", "seq_in_index") or 1,
                    raw_non_unique if raw_non_unique is not None else 1,
                )
                index_name, column_name, sequence, non_unique = values
                if not index_name or not column_name:
                    continue
                index = actual_indexes.setdefault(index_name, {"columns": [], "unique": not bool(non_unique)})
                index["columns"].append((int(sequence), column_name))
            for index in actual_indexes.values():
                index["columns"] = [item[1] for item in sorted(index["columns"])]
            absent_indexes = []
            for expected in table["indexes"]:
                actual = actual_indexes.get(expected["name"])
                if not actual or actual["columns"] != expected["columns"] or actual["unique"] != expected["unique"]:
                    absent_indexes.append(expected)
            if absent_indexes:
                missing_indexes[table_name] = absent_indexes
        mismatch = bool(missing_tables or missing_columns or missing_indexes)
        return {
            "status": "mismatch" if mismatch else "current",
            "missing_tables": missing_tables, "missing_columns": missing_columns, "missing_indexes": missing_indexes,
        }

    async def _installed_map(self, db) -> dict[str, PluginModel]:
        rows = (await db.execute(select(PluginModel))).scalars().all()
        return {row.name: row for row in rows}

    async def _pending(self, name: str) -> dict | None:
        try:
            from core.platform.plugin import plugin_platform
            return await plugin_platform.get_update_status(name)
        except Exception:
            return None

    @staticmethod
    def _version_status(disk_version: str, db_version: str, manager: PluginManager) -> str:
        if not disk_version or not db_version:
            return "unverified"
        try:
            comparison = manager.compare_versions(disk_version, db_version)
        except (ValueError, TypeError):
            return "invalid_manifest"
        if comparison > 0:
            return "version_mismatch"
        if comparison < 0:
            return "local_newer"
        return "current"

    async def _build_report(self, db, name: str, module: str, installed: PluginModel | None) -> dict:
        now = china_now()
        root = self._plugin_root(self.manager, name)
        report: dict = {
            "plugin_name": name, "module": module, "title": name,
            "db_version": str(getattr(installed, "version", "") or ""),
            "disk_version": "", "status": "unverified", "schema_status": "unverified",
            "report": {}, "expected_schema_digest": "", "repair_supported": False,
            "pending_operation_id": "", "last_scan_at": now, "error_reason": "",
        }
        manifest_path = root / "plugin.json"
        if not manifest_path.is_file():
            report.update(
                status="missing_files", report={"missing_files": [str(manifest_path)]},
                last_failure_at=now, error_reason="插件 manifest 文件缺失",
            )
            return report
        plugin_file = root / "plugin.py"
        if not plugin_file.is_file():
            report.update(
                status="missing_files", report={"missing_files": [str(plugin_file)]},
                last_failure_at=now, error_reason="插件入口文件缺失",
            )
            return report
        try:
            meta, schema = self._load_manifest(name)
            report.update(title=str(meta.get("title") or name), disk_version=str(meta.get("version") or ""), expected_schema_digest=schema_digest(schema), repair_supported=self._repair_supported(name, meta))
            constraints = meta.get("compatible_app_versions", [])
            if constraints and not self.manager.is_app_version_compatible(constraints):
                report.update(
                    status="invalid_manifest",
                    last_failure_at=now,
                    error_reason=f"插件不兼容当前应用版本: {constraints}",
                    report={
                        "compatibility_error": "当前应用版本不在插件兼容范围内",
                        "compatible_app_versions": constraints,
                    },
                )
                return report
        except Exception as exc:
            report.update(
                status="invalid_manifest", error_reason=str(exc),
                last_failure_at=now, report={"error": str(exc)},
            )
            return report
        if installed is None:
            report.update(status="not_installed", schema_status="unverified", report={"reason": "插件目录存在但未登记"})
            return report
        try:
            schema_report = await self.detect_schema(db, schema)
            report["schema_status"] = schema_report["status"]
            schema_report.setdefault("migration_chain", [])
            report["report"] = schema_report
        except Exception as exc:
            report.update(
                status="unverified", error_reason=str(exc),
                last_failure_at=now, report={"error": str(exc)},
            )
            return report
        version_status = self._version_status(report["disk_version"], report["db_version"], self.manager)
        # 版本升级报告同时给出可执行迁移链；纯代码升级没有脚本时返回空链。
        if version_status == "version_mismatch":
            try:
                steps = self.manager._migration_steps(
                    name, report["db_version"], report["disk_version"]
                )
                report["report"]["migration_chain"] = [step.name for step in steps]
            except Exception as exc:
                report["report"]["migration_chain"] = []
                report["report"]["migration_error"] = str(exc)
        pending = await self._pending(name)
        has_pending = bool(pending is not None and pending.get("status") == "awaiting_restart")
        if has_pending and pending is not None:
            report["pending_operation_id"] = pending.get("operation_id", "")
        status = resolve_status(
            awaiting_restart=has_pending,
            version_mismatch=version_status == "version_mismatch",
            local_newer=version_status == "local_newer",
            schema_mismatch=version_status == "current" and report["schema_status"] == "mismatch",
            unverified=version_status == "unverified",
            invalid_manifest=version_status == "invalid_manifest",
        )
        report["status"] = status
        report["last_success_at"] = now
        return report

    async def _save_report(self, db, report: dict) -> None:
        name = report["plugin_name"]
        row = (await db.execute(select(PluginDatabaseStateModel).where(PluginDatabaseStateModel.plugin_name == name))).scalar_one_or_none()
        report_copy = dict(report)
        report_copy.pop("report_json", None)
        report_copy["report_json"] = report_json(report.get("report", {}))
        if row is None:
            row = PluginDatabaseStateModel.from_report(report_copy)
            db.add(row)
        else:
            for key, value in report_copy.items():
                if hasattr(row, key) and key != "plugin_name":
                    setattr(row, key, value)

    async def refresh_plugin_state(self, db, name: str) -> dict:
        """在已有事务中刷新单个插件快照，供停机升级完成后立即收敛状态。"""
        installed = (await db.execute(
            select(PluginModel).where(PluginModel.name == name)
        )).scalar_one_or_none()
        discovered = next(
            (item for item in self.manager.discover() if item.get("name") == name),
            None,
        )
        if discovered:
            report = await self._build_report(
                db, name, str(discovered.get("module") or ""), installed,
            )
        else:
            module_name = str(getattr(installed, "module", "") or "")
            missing_manifest = (
                self.manager.plugins_dir / module_name / name / "plugin.json"
                if module_name else self.manager.plugins_dir / name / "plugin.json"
            )
            report = {
                "plugin_name": name, "module": module_name,
                "title": getattr(installed, "title", name),
                "db_version": str(getattr(installed, "version", "") or ""),
                "disk_version": "", "status": "missing_files",
                "schema_status": "unverified",
                "report": {"missing_files": [str(missing_manifest)]},
                "error_reason": "插件目录或 plugin.json 缺失",
                "expected_schema_digest": "", "repair_supported": False,
                "pending_operation_id": "", "last_failure_at": china_now(),
                "last_scan_at": china_now(),
            }
        await self._save_report(db, report)
        return report

    async def scan(self, *, task_id: str | None = None) -> dict:
        """扫描全部磁盘插件及已登记但缺失目录的插件。"""
        if self.__class__._active_task_id and self.__class__._active_task_id != task_id:
            return {"task_id": self.__class__._active_task_id, "reused": True}
        task_id = task_id or f"plugin-db-scan-{hashlib.sha256(str(china_now()).encode()).hexdigest()[:16]}"
        self.__class__._active_task_id = task_id
        try:
            async with async_session_factory() as db:
                installed = await self._installed_map(db)
                discovered = {item["name"]: item for item in self.manager.discover()}
                reports = []
                for name, item in discovered.items():
                    reports.append(await self._build_report(db, name, item.get("module", ""), installed.get(name)))
                # 目录中存在入口代码但缺少 manifest 的插件无法被 PluginManager.discover
                # 纳管；单独记录为文件异常，避免这类目录静默消失在体检结果中。
                for module_name in PLUGIN_MODULES:
                    module_dir = self.manager.plugins_dir / module_name
                    if not module_dir.is_dir():
                        continue
                    for plugin_dir in module_dir.iterdir():
                        if (
                            not plugin_dir.is_dir() or plugin_dir.name.startswith(("_", "."))
                            or plugin_dir.name in discovered or plugin_dir.name in installed
                            or not (plugin_dir / "plugin.py").is_file()
                        ):
                            continue
                        reports.append({
                            "plugin_name": plugin_dir.name, "module": module_name,
                            "title": plugin_dir.name, "db_version": "", "disk_version": "",
                            "status": "missing_files", "schema_status": "unverified",
                            "report": {"missing_files": [str(plugin_dir / "plugin.json")]},
                            "error_reason": "插件目录存在但缺少 plugin.json，尚未纳管",
                            "expected_schema_digest": "", "repair_supported": False,
                            "pending_operation_id": "", "last_failure_at": china_now(),
                            "last_scan_at": china_now(),
                        })
                for name, row in installed.items():
                    if name in discovered or name in _CORE_PLUGIN_NAMES:
                        continue
                    module_name = str(getattr(row, "module", "") or "")
                    missing_manifest = (
                        self.manager.plugins_dir / module_name / name / "plugin.json"
                        if module_name else self.manager.plugins_dir / name / "plugin.json"
                    )
                    reports.append({
                        "plugin_name": name, "module": module_name,
                        "title": getattr(row, "title", name), "db_version": str(row.version or ""),
                        "disk_version": "", "status": "missing_files", "schema_status": "unverified",
                        "report": {"missing_files": [str(missing_manifest)]},
                        "error_reason": "插件目录或 plugin.json 缺失",
                        "expected_schema_digest": "", "repair_supported": False,
                        "pending_operation_id": "", "last_failure_at": china_now(),
                        "last_scan_at": china_now(),
                    })
                for report in reports:
                    await self._save_report(db, report)
                # 全量扫描成功后清理已不再发现的插件快照；扫描失败时保留旧快照，避免误删诊断数据。
                report_names = {str(report.get("plugin_name") or "") for report in reports}
                if report_names:
                    await db.execute(delete(PluginDatabaseStateModel).where(
                        ~PluginDatabaseStateModel.plugin_name.in_(report_names)
                    ))
                else:
                    await db.execute(delete(PluginDatabaseStateModel))
                await db.commit()
            self.__class__._last_scan_at = china_now()
            self.__class__._last_scan_failure = None
            counts = {status: sum(1 for report in reports if report.get("status") == status) for status in STATUS_PRIORITY}
            try:
                from core.platform.audit import audit_log
                await audit_log("插件数据库体检完成", "plugin_database_scan", phase="scan", result="success")
            except Exception:
                logger.debug("插件数据库扫描审计写入失败", exc_info=True)
            return {"task_id": task_id, "reused": False, "scanned_at": self.__class__._last_scan_at, "summary": counts, "list": reports}
        except Exception as exc:
            self.__class__._last_scan_failure = {"at": china_now(), "reason": str(exc)}
            logger.exception("插件数据库扫描失败，保留上一份成功快照")
            return {
                "task_id": task_id, "reused": False, "failed": True,
                "error_reason": str(exc), "last_success_at": self.__class__._last_scan_at,
            }
        finally:
            self.__class__._active_task_id = None

    async def list_status(self, page: int = 1, limit: int = 20, module: str = "", status: str = "", keyword: str = "") -> dict:
        async with async_session_factory() as db:
            query = select(PluginDatabaseStateModel)
            if module:
                query = query.where(PluginDatabaseStateModel.module == module)
            if status:
                query = query.where(PluginDatabaseStateModel.status == status)
            rows = (await db.execute(query.order_by(PluginDatabaseStateModel.plugin_name))).scalars().all()
        values = [state_dict(row) for row in rows if not keyword or keyword.casefold() in f"{row.plugin_name} {row.title}".casefold()]
        start = max(page - 1, 0) * limit
        return {"total": len(values), "page": page, "limit": limit, "list": values[start:start + limit]}

    async def get_status(self, name: str) -> dict | None:
        async with async_session_factory() as db:
            row = (await db.execute(select(PluginDatabaseStateModel).where(PluginDatabaseStateModel.plugin_name == name))).scalar_one_or_none()
            return state_dict(row) if row else None

    async def prepare_repair(self, name: str, *, identity: str = "system") -> dict:
        state = await self.get_status(name)
        if not state:
            raise LookupError("插件不存在")
        if state.get("status") != "schema_mismatch":
            raise ValueError("当前插件没有可修复的同版本结构漂移")
        if not state.get("repair_supported"):
            raise NotImplementedError("插件未提供数据库修复钩子")
        from uuid import uuid4
        from core.platform.plugin import plugin_platform
        from core.platform.plugin_update_store import load_persisted_plan, persist_plan
        pending_id = str(state.get("pending_operation_id") or "")
        if pending_id:
            pending = await load_persisted_plan(plugin_platform, pending_id)
            if pending and pending.get("operation_type") == "repair":
                if pending.get("status") in {"prepared", "awaiting_restart"}:
                    return pending
                raise RuntimeError("该插件已有修复计划正在处理")
        plan = {
            "operation_id": f"plugin-repair-{uuid4().hex}", "plugin_id": name, "plugin_name": name,
            "owner": name, "current_version": state.get("db_version", ""), "target_version": state.get("db_version", ""),
            "package_ref": "", "package_digest": "", "package_module": state.get("module", ""),
            "status": "prepared", "restart_required": False, "identity": identity,
            "created_at": china_now().isoformat(), "confirmed_at": None, "applied_at": None,
            "error_reason": "", "confirmed_by": "", "task_id": None, "operation_type": "repair",
            "diagnostics_json": report_json(state.get("report", {})),
        }
        await persist_plan(plan)
        async with async_session_factory() as db:
            row = (await db.execute(select(PluginDatabaseStateModel).where(
                PluginDatabaseStateModel.plugin_name == name
            ))).scalar_one_or_none()
            if row:
                row.pending_operation_id = plan["operation_id"]
                await db.commit()
        return plan


plugin_database_service = PluginDatabaseService()


async def scan_plugin_database(**kwargs) -> dict:
    """公开门面：扫描全部插件数据库状态。"""
    return await plugin_database_service.scan(**kwargs)
