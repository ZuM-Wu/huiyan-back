# -*- coding: utf-8 -*-
"""AgentScope 硬切迁移。

开发阶段不迁移旧 AI 数据：旧会话、消息、凭据、技能和 MCP 配置全部删除，
AgentScope 资源重新从空库开始。迁移只执行一次，后续启动不会恢复旧表。
"""
import json
from sqlalchemy import text


_MARKER = "agentscope-hard-cut-v1"
_OLD_TABLES = (
    "hy_ai_message",
    "hy_ai_conversation",
    "hy_ai_skill",
    "hy_ai_mcp_server",
    "hy_ai_farmer_mcp_server",
    "hy_farmer_mcp_credential",
)
_RESOURCE_TABLES = ("messages", "sessions", "schedules", "teams", "skills", "mcps", "credentials", "agents")


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table"
    ), {"table": table})
    return bool(result.scalar())


async def probe(db) -> bool:
    if not await _table_exists(db, "hy_agentscope_hard_cut"):
        return False
    result = await db.execute(text(
        "SELECT COUNT(*) FROM hy_agentscope_hard_cut WHERE marker=:marker"
    ), {"marker": _MARKER})
    return bool(result.scalar())


async def _drop_table(db, table: str) -> None:
    if await _table_exists(db, table):
        await db.execute(text(f"DROP TABLE `{table}`"))


async def _seed_model_cards(db) -> None:
    cards = (
        ("deepseek_credential", "deepseek-chat", "DeepSeek Chat", ["text/plain"], ["text/plain"], 65536, 8192, 1, 0, 0),
        ("deepseek_credential", "deepseek-reasoner", "DeepSeek Reasoner", ["text/plain"], ["text/plain", "application/x-thinking"], 65536, 8192, 1, 1, 0),
        ("glm_credential", "glm-5.2", "GLM-5.2", ["text/plain"], ["text/plain", "application/x-thinking"], 128000, 8192, 1, 1, 0),
        ("glm_credential", "glm-4.6v-flash", "GLM-4.6V-Flash", ["text/plain", "image/*"], ["text/plain", "application/x-thinking"], 128000, 8192, 1, 1, 1),
        ("openai_credential", "gpt-4.1", "GPT-4.1", ["text/plain", "image/*"], ["text/plain"], 1047576, 32768, 1, 0, 1),
        ("openai_credential", "gpt-5.5", "GPT-5.5", ["text/plain", "image/*"], ["text/plain", "application/x-thinking"], 1050000, 32768, 1, 1, 1),
    )
    for provider, name, label, inputs, outputs, context_size, output_size, tools, reasoning, vision in cards:
        await db.execute(text(
            "INSERT INTO hy_agentscope_model_card "
            "(provider, model_name, label, input_types, output_types, context_size, output_size, "
            "support_tools, support_reasoning, support_vision, parameter_schema) "
            "VALUES (:provider, :name, :label, :inputs, :outputs, :context_size, :output_size, "
            ":tools, :reasoning, :vision, :schema)"
        ), {
            "provider": provider, "name": name, "label": label,
            "inputs": json.dumps(inputs, ensure_ascii=False),
            "outputs": json.dumps(outputs, ensure_ascii=False),
            "context_size": context_size, "output_size": output_size,
            "tools": tools, "reasoning": reasoning, "vision": vision,
            "schema": json.dumps({"type": "object", "properties": {"temperature": {"type": "number"}}}, ensure_ascii=False),
        })


async def apply(db) -> None:
    # 迁移本身也必须可直接重复调用：注册表之外的集成测试、运维脚本
    # 可能直接调用 apply，已完成时不得重建目录或清空管理员新录入的数据。
    if await probe(db):
        return

    for table in _OLD_TABLES:
        await _drop_table(db, table)

    for table in _RESOURCE_TABLES:
        if await _table_exists(db, table):
            await db.execute(text(f"DELETE FROM `{table}`"))

    if await _table_exists(db, "hy_configuration"):
        await db.execute(text(
            "DELETE FROM hy_configuration WHERE `key` LIKE 'ai.%' "
            "OR `key` LIKE 'llm_deepseek.%' OR `key` LIKE 'vision_glm.%'"
        ))
    if await _table_exists(db, "hy_plugin"):
        await db.execute(text(
            "DELETE FROM hy_plugin WHERE name IN ('llm_deepseek', 'vision_glm')"
        ))

    await db.execute(text("DROP TABLE IF EXISTS hy_agentscope_model_card"))
    await db.execute(text("DROP TABLE IF EXISTS hy_agentscope_connection_pool"))
    await db.execute(text(
        "CREATE TABLE hy_agentscope_model_card ("
        "id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
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
        "PRIMARY KEY (id), UNIQUE KEY uq_agentscope_model_provider_name (provider, model_name)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope 可编辑 ModelCard 目录'"
    ))
    await db.execute(text(
        "CREATE TABLE hy_agentscope_connection_pool ("
        "id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',"
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
    await _seed_model_cards(db)
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS hy_agentscope_hard_cut ("
        "marker VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '硬切迁移标记',"
        "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间'"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AgentScope 硬切迁移记录'"
    ))
    await db.execute(text(
        "INSERT IGNORE INTO hy_agentscope_hard_cut(marker) VALUES (:marker)"
    ), {"marker": _MARKER})
