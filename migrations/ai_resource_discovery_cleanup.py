# -*- coding: utf-8 -*-
"""AI 资源模型发现结果与历史 ModelCard 清理迁移。"""
from sqlalchemy import text


_MARKER = "ai-resource-discovery-cleanup-v1"


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


async def _create_discovery_table(db) -> None:
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_agentscope_connection_model_discovery ("
        "id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "connection_id BIGINT NOT NULL COMMENT '来源连接池 ID',"
        "model_name VARCHAR(128) NOT NULL COMMENT '远端模型 ID',"
        "label VARCHAR(128) NOT NULL DEFAULT '' COMMENT '模型展示名称',"
        "input_types TEXT NOT NULL COMMENT '输入模态 JSON 数组',"
        "output_types TEXT NOT NULL COMMENT '输出能力 JSON 数组',"
        "context_size INT NOT NULL DEFAULT 32768 COMMENT '上下文长度',"
        "output_size INT NOT NULL DEFAULT 4096 COMMENT '最大输出长度',"
        "support_tools TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持工具调用',"
        "support_reasoning TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持思考',"
        "support_vision TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持视觉输入',"
        "parameter_schema TEXT NOT NULL COMMENT '模型参数 Schema JSON',"
        "discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发现时间',"
        "PRIMARY KEY (id),"
        "UNIQUE KEY uq_agentscope_connection_discovery_model (connection_id, model_name),"
        "KEY ix_agentscope_connection_discovery_connection (connection_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope 连接模型发现结果'"
    ))


async def apply(db) -> None:
    if await probe(db):
        return
    await _create_discovery_table(db)
    if await _table_exists(db, "hy_agentscope_model_card"):
        # 历史 ModelCard 没有经过真实模型发现，无法证明来源可信，迁移时统一清理。
        await db.execute(text("DELETE FROM hy_agentscope_model_card"))
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_ai_resource_refactor ("
        "marker VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '迁移标记',"
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 资源连接来源迁移记录'"
    ))
    await db.execute(text(
        "INSERT IGNORE INTO hy_ai_resource_refactor(marker) VALUES (:marker)"
    ), {"marker": _MARKER})
