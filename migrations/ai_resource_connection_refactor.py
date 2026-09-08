# -*- coding: utf-8 -*-
"""AI 资源连接来源与 ModelCard 关联迁移。"""
from sqlalchemy import text


_MARKER = "ai-resource-connection-refactor-v1"


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
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_ai_resource_refactor WHERE marker=:marker"
    ), {"marker": _MARKER})
    return bool(result.scalar())


async def _create_model_cards(db) -> None:
    await db.execute(text("DROP TABLE IF EXISTS hy_agentscope_model_card"))
    await db.execute(text(
        "CREATE TABLE hy_agentscope_model_card ("
        "id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
        "connection_id BIGINT NOT NULL COMMENT '来源连接池 ID',"
        "provider VARCHAR(64) NOT NULL COMMENT 'Credential Provider 类型',"
        "model_name VARCHAR(128) NOT NULL COMMENT '模型 ID',"
        "label VARCHAR(128) NOT NULL DEFAULT '' COMMENT '模型展示名称',"
        "input_types TEXT NOT NULL COMMENT '输入模态 JSON 数组',"
        "output_types TEXT NOT NULL COMMENT '输出能力 JSON 数组',"
        "context_size INT NOT NULL DEFAULT 32768 COMMENT '上下文长度',"
        "output_size INT NOT NULL DEFAULT 4096 COMMENT '最大输出长度',"
        "support_tools TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持工具调用',"
        "support_reasoning TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持思考',"
        "support_vision TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持视觉输入',"
        "parameter_schema TEXT NOT NULL COMMENT '模型参数 Schema JSON',"
        "status TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用，2停用',"
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
        "PRIMARY KEY (id), UNIQUE KEY uq_agentscope_model_connection_name (connection_id, model_name)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope 可编辑 ModelCard 目录'"
    ))


async def _prepare_connections(db) -> None:
    if not await _table_exists(db, "hy_agentscope_connection_pool"):
        await db.execute(text(
            "CREATE TABLE hy_agentscope_connection_pool ("
            "id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
            "preset_key VARCHAR(64) NOT NULL DEFAULT 'custom_openai' COMMENT '连接预设键',"
            "protocol VARCHAR(64) NOT NULL DEFAULT 'openai_chat_completions' COMMENT '连接协议类型',"
            "provider VARCHAR(64) NOT NULL COMMENT 'Credential Provider 类型',"
            "credential_id VARCHAR(128) NOT NULL COMMENT 'AgentScope Credential ID',"
            "name VARCHAR(128) NOT NULL DEFAULT '' COMMENT '连接显示名称',"
            "default_model VARCHAR(128) NOT NULL DEFAULT '' COMMENT '默认模型 ID',"
            "status TINYINT NOT NULL DEFAULT 1 COMMENT '状态：1启用，2停用',"
            "is_default TINYINT NOT NULL DEFAULT 0 COMMENT '是否默认连接：0否，1是',"
            "last_test_status TINYINT NOT NULL DEFAULT 0 COMMENT '测试状态：0未测，1成功，2失败',"
            "last_test_error VARCHAR(512) NOT NULL DEFAULT '' COMMENT '最近测试错误',"
            "last_test_at DATETIME NULL COMMENT '最近测试时间',"
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
            "PRIMARY KEY (id), UNIQUE KEY uq_agentscope_connection_credential (credential_id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope Credential 模型连接池'"
        ))
        return
    if not await _column_exists(db, "hy_agentscope_connection_pool", "preset_key"):
        await db.execute(text(
            "ALTER TABLE hy_agentscope_connection_pool ADD COLUMN preset_key VARCHAR(64) "
            "NOT NULL DEFAULT 'custom_openai' COMMENT '连接预设键'"
        ))
    if not await _column_exists(db, "hy_agentscope_connection_pool", "protocol"):
        await db.execute(text(
            "ALTER TABLE hy_agentscope_connection_pool ADD COLUMN protocol VARCHAR(64) "
            "NOT NULL DEFAULT 'openai_chat_completions' COMMENT '连接协议类型'"
        ))
    await db.execute(text(
        "UPDATE hy_agentscope_connection_pool SET preset_key='deepseek', protocol='deepseek' "
        "WHERE provider='deepseek_credential'"
    ))
    await db.execute(text(
        "UPDATE hy_agentscope_connection_pool SET preset_key='glm', protocol='glm' "
        "WHERE provider='glm_credential'"
    ))
    await db.execute(text(
        "UPDATE hy_agentscope_connection_pool SET preset_key='custom_openai', protocol='openai_chat_completions' "
        "WHERE provider='openai_credential'"
    ))


async def apply(db) -> None:
    if await probe(db):
        return
    await _prepare_connections(db)
    await _create_model_cards(db)
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_ai_resource_refactor ("
        "marker VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '迁移标记',"
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 资源连接来源迁移记录'"
    ))
    await db.execute(text(
        "INSERT IGNORE INTO hy_ai_resource_refactor(marker) VALUES (:marker)"
    ), {"marker": _MARKER})
