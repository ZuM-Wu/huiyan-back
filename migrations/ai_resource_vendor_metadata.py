# -*- coding: utf-8 -*-
"""AI 连接供应商名称与标准协议元数据迁移。"""
from sqlalchemy import text


_MARKER = "ai-resource-vendor-metadata-v1"


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
    ), {"table": table})
    return bool(result.scalar())


async def _column_exists(db, table: str, column: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table AND COLUMN_NAME=:column"
    ), {"table": table, "column": column})
    return bool(result.scalar())


async def probe(db) -> bool:
    if not await _table_exists(db, "hy_ai_resource_refactor"):
        return False
    if not await _table_exists(db, "hy_agentscope_connection_pool"):
        return False
    if not await _column_exists(db, "hy_agentscope_connection_pool", "vendor_name"):
        return False
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_ai_resource_refactor WHERE marker=:marker"
    ), {"marker": _MARKER})
    return bool(result.scalar())


async def apply(db) -> None:
    if await probe(db):
        return
    if await _table_exists(db, "hy_agentscope_connection_pool"):
        if not await _column_exists(db, "hy_agentscope_connection_pool", "vendor_name"):
            await db.execute(text(
                "ALTER TABLE hy_agentscope_connection_pool ADD COLUMN vendor_name VARCHAR(128) "
                "NOT NULL DEFAULT '' COMMENT '供应商显示名称'"
            ))
        await db.execute(text(
            "UPDATE hy_agentscope_connection_pool SET "
            "protocol='openai_chat_completions', "
            "vendor_name=CASE "
            "WHEN provider='deepseek_credential' THEN 'DeepSeek' "
            "WHEN provider='glm_credential' THEN '智谱 GLM' "
            "WHEN (vendor_name IS NULL OR vendor_name='') AND provider='openai_credential' THEN '自定义供应商' "
            "ELSE vendor_name END "
            "WHERE protocol <> 'openai_chat_completions' OR protocol IS NULL "
            "OR vendor_name IS NULL OR vendor_name=''"
        ))
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_ai_resource_refactor ("
        "marker VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '迁移标记',"
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 资源连接来源迁移记录'"
    ))
    await db.execute(text(
        "INSERT IGNORE INTO hy_ai_resource_refactor(marker) VALUES (:marker)"
    ), {"marker": _MARKER})
