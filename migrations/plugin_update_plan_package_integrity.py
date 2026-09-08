# -*- coding: utf-8 -*-
"""为存量插件更新计划补齐暂存包完整性字段。"""

from sqlalchemy import text


async def _column_exists(db, name: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_plugin_update_plan' "
        "AND COLUMN_NAME=:name"
    ), {"name": name})
    return bool(result.scalar())


async def probe(db) -> bool:
    """两个完整性字段均存在时视为迁移完成。"""
    return await _column_exists(db, "package_digest") and await _column_exists(db, "package_module")


async def apply(db) -> None:
    """幂等增加摘要与模块字段；旧计划因没有可信快照统一失效。"""
    if not await _column_exists(db, "package_digest"):
        await db.execute(text(
            "ALTER TABLE hy_plugin_update_plan ADD COLUMN package_digest VARCHAR(64) "
            "NOT NULL DEFAULT '' COMMENT '暂存插件包 SHA-256 摘要' AFTER package_ref"
        ))
    if not await _column_exists(db, "package_module"):
        await db.execute(text(
            "ALTER TABLE hy_plugin_update_plan ADD COLUMN package_module VARCHAR(32) "
            "NOT NULL DEFAULT '' COMMENT '插件所属模块目录' AFTER package_digest"
        ))
    await db.execute(text(
        "UPDATE hy_plugin_update_plan SET status='failed', restart_required=0, "
        "error_reason='旧更新计划缺少可信暂存包，请重新预检' "
        "WHERE status IN ('prepared', 'awaiting_restart') "
        "AND (package_digest='' OR package_module='')"
    ))
