# -*- coding: utf-8 -*-
"""将旧版内存更新确认记录幂等回填到持久化计划表。"""

import re

from sqlalchemy import text


_FIELD = re.compile(r"(?P<key>owner|version|current_version|operation_id)=([^\s\]]+)")


async def probe(db) -> bool:
    """登记表已有本迁移名时视为完成，避免重复扫描历史日志。"""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_schema_version "
        "WHERE name='plugin_update_plan_legacy_backfill'"
    ))
    return result.scalar() == 0


async def apply(db) -> None:
    """按插件和时间倒序回填最近一次成功确认，跳过已应用/失败计划。"""
    rows = (await db.execute(text(
        "SELECT l.description, l.user_name, l.create_time "
        "FROM hy_system_log l WHERE l.type='plugin_update_confirm' "
        "ORDER BY l.create_time DESC, l.id DESC"
    ))).all()
    seen = set()
    for description, user_name, created_at in rows:
        values = {match.group("key"): match.group(2) for match in _FIELD.finditer(description or "")}
        plugin_name = values.get("owner", "")
        operation_id = values.get("operation_id", "")
        if not plugin_name or not operation_id or plugin_name in seen:
            continue
        seen.add(plugin_name)
        current = values.get("current_version", "")
        target = values.get("version", "")
        if not current or not target:
            continue
        await db.execute(text(
            "INSERT IGNORE INTO hy_plugin_update_plan "
            "(operation_id, plugin_name, current_version, target_version, package_ref, status, "
            "restart_required, created_at, confirmed_at, error_reason, identity, confirmed_by) "
            "SELECT :operation_id, :plugin_name, :current_version, :target_version, '', "
            "'awaiting_restart', 1, :created_at, :created_at, '', 'legacy', :confirmed_by "
            "FROM hy_plugin p WHERE p.name=:plugin_name AND p.version=:current_version"
        ), {
            "operation_id": operation_id, "plugin_name": plugin_name,
            "current_version": current, "target_version": target,
            "created_at": created_at, "confirmed_by": user_name or "legacy",
        })
