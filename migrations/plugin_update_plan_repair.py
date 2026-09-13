"""为插件更新计划增加升级/修复类型和诊断摘要。"""

from sqlalchemy import text


async def _column_exists(db, column: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_plugin_update_plan' "
        "AND COLUMN_NAME=:column"
    ), {"column": column})
    return bool(result.scalar())


async def probe(db) -> bool:
    return await _column_exists(db, "operation_type") and await _column_exists(db, "diagnostics_json")


async def apply(db) -> None:
    if not await _column_exists(db, "operation_type"):
        await db.execute(text(
            "ALTER TABLE hy_plugin_update_plan ADD COLUMN operation_type VARCHAR(16) NOT NULL DEFAULT 'upgrade' COMMENT '操作类型: upgrade/repair'"
        ))
    if not await _column_exists(db, "diagnostics_json"):
        await db.execute(text(
            "ALTER TABLE hy_plugin_update_plan ADD COLUMN diagnostics_json TEXT NOT NULL COMMENT '体检报告摘要 JSON'"
        ))
