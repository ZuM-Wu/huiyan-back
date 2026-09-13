"""插件数据库声明校验、状态优先级和数据库结果适配工具。"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from typing import Any

from core.db.plugin_database_state import PluginDatabaseStateModel

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
STATUS_PRIORITY = (
    "missing_files", "invalid_manifest", "not_installed", "unverified",
    "awaiting_restart", "version_mismatch", "local_newer", "schema_mismatch", "current",
)


def resolve_status(
    *, missing_files: bool = False, invalid_manifest: bool = False,
    not_installed: bool = False, unverified: bool = False,
    awaiting_restart: bool = False, version_mismatch: bool = False,
    local_newer: bool = False, schema_mismatch: bool = False,
) -> str:
    """按平台约定的固定优先级合并多个异常信号。"""
    flags = {
        "missing_files": missing_files, "invalid_manifest": invalid_manifest,
        "not_installed": not_installed, "unverified": unverified,
        "awaiting_restart": awaiting_restart, "version_mismatch": version_mismatch,
        "local_newer": local_newer, "schema_mismatch": schema_mismatch,
    }
    for status in STATUS_PRIORITY:
        if flags.get(status):
            return status
    return "current"


def validate_database_schema(value: Any) -> dict:  # noqa: C901, PLR0912
    """校验并规范化 manifest.database_schema。"""
    if value is None:
        return {"version": 1, "tables": []}
    if not isinstance(value, dict):
        raise ValueError("database_schema 必须是对象")
    version = value.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("database_schema.version 必须是正整数")
    tables = value.get("tables", [])
    if not isinstance(tables, list):
        raise ValueError("database_schema.tables 必须是列表")
    normalized: list[dict] = []
    seen_tables: set[str] = set()
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise ValueError("database_schema.tables 项必须包含表名")
        name = table["name"].strip()
        if not _NAME_RE.fullmatch(name) or name in seen_tables:
            raise ValueError(f"非法或重复表名: {name}")
        seen_tables.add(name)
        columns = table.get("columns", [])
        indexes = table.get("indexes", [])
        if not isinstance(columns, list) or any(
            not isinstance(item, str) or not _NAME_RE.fullmatch(item) for item in columns
        ):
            raise ValueError(f"表 {name} 的 columns 声明无效")
        if len(set(columns)) != len(columns):
            raise ValueError(f"表 {name} 存在重复列")
        if not isinstance(indexes, list):
            raise ValueError(f"表 {name} 的 indexes 必须是列表")
        normalized_indexes = []
        seen_indexes: set[str] = set()
        for index in indexes:
            if not isinstance(index, dict) or not isinstance(index.get("name"), str):
                raise ValueError(f"表 {name} 的索引声明无效")
            index_name = index["name"].strip()
            index_columns = index.get("columns", [])
            if not _NAME_RE.fullmatch(index_name) or index_name in seen_indexes:
                raise ValueError(f"表 {name} 存在非法或重复索引: {index_name}")
            if not isinstance(index_columns, list) or not index_columns or any(
                not isinstance(item, str) or not _NAME_RE.fullmatch(item) for item in index_columns
            ):
                raise ValueError(f"索引 {index_name} 的列声明无效")
            if any(item not in columns for item in index_columns):
                raise ValueError(f"索引 {index_name} 引用了未声明列")
            if "unique" in index and not isinstance(index["unique"], bool):
                raise ValueError(f"索引 {index_name} 的 unique 必须是布尔值")
            seen_indexes.add(index_name)
            normalized_indexes.append({
                "name": index_name, "columns": list(index_columns),
                "unique": bool(index.get("unique", False)),
            })
        normalized.append({"name": name, "columns": list(columns), "indexes": normalized_indexes})
    return {"version": version, "tables": normalized}


def schema_digest(schema: dict) -> str:
    """计算稳定的声明式结构摘要。"""
    normalized = validate_database_schema(schema)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def report_json(report: dict) -> str:
    """序列化扫描报告，统一处理时间等非 JSON 原生值。"""
    return json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)


def state_dict(row: PluginDatabaseStateModel) -> dict:
    """将状态 ORM 行转换为 API 可用字典。"""
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    try:
        data["report"] = json.loads(data.get("report_json") or "{}")
    except json.JSONDecodeError:
        data["report"] = {"error": data.get("report_json", "")}
    return data


def row_value(row: Any, index: int, *names: str) -> Any:
    """兼容 SQLAlchemy Row、mapping 和轻量测试 double 的字段读取。"""
    if isinstance(row, (tuple, list)):
        return row[index] if len(row) > index else None
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        for name in names:
            if name in mapping:
                return mapping[name]
    if isinstance(row, dict):
        for name in names:
            if name in row:
                return row[name]
    for name in names:
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


async def result_all(result: Any) -> list:
    """读取结果集，兼容同步 Result 与异步测试 double。"""
    rows = result.all()
    if inspect.isawaitable(rows):
        rows = await rows
    return list(rows or [])


async def result_scalar(result: Any) -> Any:
    """读取标量结果，兼容缺少 scalar() 的最小 fake Result。"""
    scalar = getattr(result, "scalar", None)
    if callable(scalar):
        value = scalar()
        return await value if inspect.isawaitable(value) else value
    one = getattr(result, "one", None)
    if callable(one):
        value = one()
        value = await value if inspect.isawaitable(value) else value
        return row_value(value, 0, "count", "COUNT(*)")
    return None
