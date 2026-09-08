# -*- coding: utf-8 -*-
"""统一 AgentScope ModelCard 与发现快照的视觉能力字段。"""
from __future__ import annotations

from sqlalchemy import text


_MARKER = "ai-model-card-vision-consistency-v1"
_TABLES = (
    "hy_agentscope_model_card",
    "hy_agentscope_connection_model_discovery",
)


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
    ), {"table": table})
    return bool(result.scalar())


async def probe(db) -> bool:
    if not await _table_exists(db, "hy_ai_resource_refactor"):
        return False
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_ai_resource_refactor WHERE marker=:marker"
    ), {"marker": _MARKER})
    return bool(result.scalar())


async def _normalize_table(db, table: str) -> None:
    """先兼容旧视觉布尔值补齐模态，再由模态派生最终布尔值。"""
    if not await _table_exists(db, table):
        return
    await db.execute(text(
        f"UPDATE {table} SET input_types=CASE "
        "WHEN support_vision=1 AND JSON_VALID(input_types)=0 "
        "THEN JSON_ARRAY('text/plain', 'image/*') "
        "WHEN support_vision=1 AND JSON_CONTAINS(CAST(input_types AS JSON), JSON_QUOTE('image/*'))=0 "
        "THEN JSON_ARRAY_APPEND(CAST(input_types AS JSON), '$', 'image/*') "
        "ELSE input_types END"
    ))
    await db.execute(text(
        f"UPDATE {table} SET support_vision=CASE "
        "WHEN JSON_VALID(input_types)=1 "
        "AND JSON_CONTAINS(CAST(input_types AS JSON), JSON_QUOTE('image/*'))=1 "
        "THEN 1 ELSE 0 END"
    ))


async def apply(db) -> None:
    """幂等修复存量矛盾记录，不修改连接、Credential 或会话消息。"""
    if await probe(db):
        return
    for table in _TABLES:
        await _normalize_table(db, table)
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_ai_resource_refactor ("
        "marker VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '迁移标记',"
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 资源连接来源迁移记录'"
    ))
    await db.execute(text(
        "INSERT IGNORE INTO hy_ai_resource_refactor(marker) VALUES (:marker)"
    ), {"marker": _MARKER})
