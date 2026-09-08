# -*- coding: utf-8 -*-
"""将存量插件版本归一到 1.x.z 版本轨道。"""

from sqlalchemy import text


TARGET_VERSIONS = {
    "admin_notifier": ("2.0.0", "1.0.1"),
    "app_manage": ("2.0.0", "1.0.1"),
    "file_download": ("2.0.0", "1.0.1"),
    "hello_world": ("3.0.0", "1.0.1"),
    "knowledge": ("2.0.0", "1.0.1"),
    "push": ("3.0.0", "1.0.1"),
    "wecom_webhook": ("5.0.0", "1.0.1"),
    "xiaozhi_mcp": ("2.0.0", "1.0.1"),
    "zhima_credit": ("1.0.0", "1.0.1"),
    "mail_smtp": ("1.0.0", "1.0.1"),
    "local_oss": ("1.0.0", "1.0.1"),
    "sms_idcsmart": ("1.0.0", "1.0.1"),
    "weather_amap": ("1.0.0", "1.0.1"),
    "weather_qweather": ("1.1.0", "1.1.1"),
}

_PLAN_STATUSES = ("prepared", "awaiting_restart")
_PLAN_REASON = "插件版本轨道归一：旧版本更新计划已失效，请按新版本重新预检"


async def _table_exists(db, table_name: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table_name"
    ), {"table_name": table_name})
    return bool(result.scalar())


async def probe(db) -> bool:
    """所有已安装目标插件均已处于目标版本，或尚未安装。"""
    if not await _table_exists(db, "hy_plugin"):
        return True
    for plugin_name, (old_version, target_version) in TARGET_VERSIONS.items():
        result = await db.execute(text(
            "SELECT version FROM hy_plugin WHERE name=:plugin_name"
        ), {"plugin_name": plugin_name})
        version = result.scalar_one_or_none()
        if version == old_version:
            return False
        if version and version != target_version:
            # 未知版本不由一次性迁移强行覆盖，交由普通升级流程报告。
            continue
    return True


async def apply(db) -> None:
    """精确更新已知旧版本，并废弃受影响的旧重启计划。"""
    if await _table_exists(db, "hy_plugin"):
        for plugin_name, (old_version, target_version) in TARGET_VERSIONS.items():
            await db.execute(text(
                "UPDATE hy_plugin SET version=:target_version "
                "WHERE name=:plugin_name AND version=:old_version"
            ), {
                "plugin_name": plugin_name,
                "old_version": old_version,
                "target_version": target_version,
            })

    if not await _table_exists(db, "hy_plugin_update_plan"):
        return
    for plugin_name in TARGET_VERSIONS:
        await db.execute(text(
            "UPDATE hy_plugin_update_plan "
            "SET status='rolled_back', restart_required=0, error_reason=:reason "
            "WHERE plugin_name=:plugin_name AND status IN (:prepared, :awaiting_restart)"
        ), {
            "plugin_name": plugin_name,
            "reason": _PLAN_REASON,
            "prepared": _PLAN_STATUSES[0],
            "awaiting_restart": _PLAN_STATUSES[1],
        })
